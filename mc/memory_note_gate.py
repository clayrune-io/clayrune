"""G1 — the attended note-write gate (MEMORY_DESIGN_V2_SPEC.md §10.1/§10.2/§10.3).

Pure logic behind `POST/PATCH /api/project/<id>/memory/note`
(`mc/blueprints/memory_note_routes.py` is the thin HTTP layer). Nothing in
the codebase writes through this yet — MC-964 pre-step-10 fix B builds the
gate so the step-9 measurement has a rejection rate to read; wiring an
existing writer through it is a separate, later decision.

Two postures, one switch (`memory_gate_mode`, Condition 48, default 'report'):

* `report`  — the write lands exactly as it would have without a gate, and
  every violation that WOULD have refused it is recorded (`_log` + a JSONL row
  the step-9 harness reads via `gate_counts`).
* `enforce` — any violation refuses the write, 422, machine-readable body,
  and "a refused write leaves the on-disk file byte-for-byte untouched" (the
  413 index-cap contract this copies).

Three things are NOT mode-dependent, because they are rails and not gate
classes: slug/path safety (this route must never reach MEMORY.md, a position
file, or another project's dir), the exact-filename clobber refusal on POST
(a report-mode overwrite would destroy a curated note — `write_topic_note`'s
"never clobbers" posture), and server-side provenance stamping (an unstamped
note reads as `legacy` = attended-equivalent, which would launder an
unattended agent's output into attended memory).
"""

import difflib
import json
import re
from pathlib import Path

from flask import has_request_context, request

import mc.memory as _mem
import mc.skills as _skills
from mc import state
from mc.core import _atomic_write_text, _log, now_iso
from mc.state import agent_sessions
from mc.unattended import is_unattended_caller

NOTE_TYPES = ('user', 'feedback', 'project', 'reference')
_DEFAULT_TYPE = 'project'

# Violation classes (the §10.2 table). Stable strings: the step-9 harness
# groups `gate_counts()['by_class']` on them.
V_FM_MISSING = 'frontmatter_missing'
V_FM_UNPARSEABLE = 'frontmatter_unparseable'
V_FIELD = 'missing_field'
V_LINK_B = 'class_b_rename_without_aka'
V_LINK_C = 'class_c_cross_vault'
V_LINK_D = 'class_d_unresolved'
V_DUP = 'duplicate_identity'
# Not a violation: deterministic repair at resolution (spec: "nothing for an
# author to fix and no information in reporting it"). Counted, never refused.
R_LINK_A = 'class_a_md_in_link'

REQUIRED_FIELDS = ('name', 'description')   # type/origin are server-supplied

_SLUG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$')
_KEY_LINE_RE = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_-]*)\s*:')
_SERVER_KEYS = ('origin', 'generated', 'verified')   # never author-writable

_NON_NOTE_FILES = ('MEMORY.md', 'MEMORY_ARCHIVE.md')


class SlugRefused(ValueError):
    """The slug would reach a file this route must never write."""


# ── mode ─────────────────────────────────────────────────────────────────────

def gate_mode():
    """'enforce' only on the literal string; anything else (unset, typo,
    wrong type) is 'report' — Condition 48's default is the safe direction."""
    v = state.CONFIG.get('memory_gate_mode', 'report')
    return 'enforce' if isinstance(v, str) and v.strip().lower() == 'enforce' else 'report'


# ── caller identity → origin stamp ───────────────────────────────────────────

def classify_caller(project_id):
    """-> (origin, refusal). `origin` is 'interactive'|'unattended', derived
    from running-session state through `mc.unattended` (the single definition
    the vault/config routes use) — never from anything the caller sends.

    `refusal` is a string when an unattended caller is plausibly writing a
    project that is not its own: the standing position lets a guarded agent
    write ITS OWN project's memory topic files only. With no caller identity
    on a localhost API this is best-effort: an unattended request is allowed
    into project P only if P has a running non-manual session, or a running
    manual one (then it may be the attended agent, and the stamp is
    'unattended' regardless, fail-safe). Residual hole, stated rather than
    hidden: a foreign agent can still write P while P also has an unattended
    session running.
    """
    if has_request_context() and request.headers.get('Origin'):
        return 'interactive', None
    unatt_in_project = is_unattended_caller(project_id)
    if unatt_in_project:
        return 'unattended', None
    if not is_unattended_caller(None):
        return 'interactive', None
    manual_here = any(
        s.get('status') == 'running' and s.get('project_id') == project_id
        for s in agent_sessions.values())
    if manual_here:
        return 'unattended', None
    return 'unattended', ('an unattended session is running elsewhere and '
                          'none is running on this project')


# ── slug safety (always on) ──────────────────────────────────────────────────

def validate_slug(slug):
    """-> bare stem. Raises SlugRefused. Not a gate class: path safety."""
    s = str(slug or '').strip()
    if s.lower().endswith('.md'):
        s = s[:-3]
    if not s or not _SLUG_RE.match(s) or '..' in s:
        raise SlugRefused('slug must be a bare file stem: letters, digits, '
                          '_ - . only, no path separators')
    reserved = {Path(n).stem.lower() for n in _NON_NOTE_FILES}
    reserved |= {Path(_mem.SESSION_LOG_FILE).stem.lower(),
                 Path(_mem.CONTINUITY_FILE).stem.lower()}
    if s.lower() in reserved:
        raise SlugRefused(f'{s!r} is a reserved vault file, not a topic note')
    if s.lower().startswith(_mem.POSITION_PREFIX):
        raise SlugRefused('position_* files are written by write_position, '
                          'which supersedes in place; this route writes topic notes')
    return s


# ── frontmatter ──────────────────────────────────────────────────────────────

def _normalise(content):
    return str(content).replace('\r\n', '\n').lstrip('﻿')


def split_frontmatter(content):
    """-> (kind, block, body, meta). kind: 'ok' | 'missing' | 'unparseable'.

    'unparseable' = the document opens a fence that never closes, or the
    block holds a top-level line that is neither `key: value` nor a comment
    nor an indented continuation (the vault parser skips such a line without
    a word — a typo would silently vanish a field).
    """
    text = _normalise(content)
    if not text.startswith('---'):
        return 'missing', '', text, {}
    m = _skills._FENCE_RE.match(text)
    if not m:
        return 'unparseable', '', text, {}
    block = m.group(1)
    for ln in block.splitlines():
        if (not ln.strip() or ln.lstrip().startswith('#')
                or ln.startswith((' ', '\t')) or _KEY_LINE_RE.match(ln)):
            continue
        return 'unparseable', block, text[m.end():], {}
    meta, body = _skills.parse_skill_md(text)
    return 'ok', block, body, meta if isinstance(meta, dict) else {}


def _pop_top_level_keys(block_lines, keys):
    """-> (kept_lines, {key: [raw lines incl. the key line]}). A key owns its
    line plus every following indented line (`generated:`'s `by`/`at`,
    `verified:`'s sequence)."""
    kept, popped, cur = [], {}, None
    for ln in block_lines:
        m = _KEY_LINE_RE.match(ln)
        if m and not ln.startswith((' ', '\t')):
            cur = m.group(1) if m.group(1) in keys else None
            if cur:
                popped.setdefault(cur, []).append(ln)
                continue
        elif cur and ln.startswith((' ', '\t')):
            popped[cur].append(ln)
            continue
        else:
            cur = None
        kept.append(ln)
    return kept, popped


def _has_type(meta):
    if str(meta.get('type') or '').strip():
        return True
    return 'type:' in str(meta.get('metadata') or '')


def _list_values(raw):
    """aka / external_ref values: `[a, b]`, comma list, or one per line."""
    out = []
    for piece in re.split(r'[\n,]', str(raw or '').strip().strip('[]')):
        piece = piece.strip().lstrip('-').strip().strip('"\'')
        if piece:
            out.append(piece)
    return out


# ── vault resolution ─────────────────────────────────────────────────────────

def _head_meta(path):
    try:
        text = path.read_text(encoding='utf-8', errors='replace')[:4096]
        meta, _b = _skills.parse_skill_md(text)
        return meta if isinstance(meta, dict) else {}
    except Exception as e:
        _log(f'[memory-gate] could not read {path.name} for resolution: {e}')
        return {}


def _vault_keys(mem_dir, with_aka):
    """{link_key: stem}. R3 order — filename stem, frontmatter `name:`, then
    the `aka:` forwarding record — via the same canonicaliser every other
    resolution site uses, first claim wins so a stem is never shadowed."""
    files = [f for f in sorted(mem_dir.glob('*.md')) if f.name not in _NON_NOTE_FILES]
    out = {}
    for f in files:
        out.setdefault(_mem._mem_link_key(f.stem), f.stem)
    metas = [(f, _head_meta(f)) for f in files]
    for f, meta in metas:
        k = _mem._mem_link_key(str(meta.get('name') or ''))
        if k:
            out.setdefault(k, f.stem)
    if with_aka:
        for f, meta in metas:
            for a in _list_values(meta.get('aka')):
                k = _mem._mem_link_key(a)
                if k:
                    out.setdefault(k, f.stem)
    return out


def _foreign_vault_of(mem_dir, key):
    """Name of the sibling project vault that resolves `key`, or ''."""
    root = _mem.CLAUDE_HOME
    if root is None or not Path(root).is_dir():
        return ''
    own = mem_dir.resolve()
    for vault in sorted(Path(root).glob('*/memory')):
        if vault.resolve() == own or not vault.is_dir():
            continue
        if key in _vault_keys(vault, with_aka=False):
            return vault.parent.name
    return ''


def check_links(mem_dir, body, *, skip_targets=(), acknowledged=()):
    """-> (violations, repairs) for every `[[wikilink]]` in `body` not in
    `skip_targets` (on PATCH: targets the note already carried — §10.4 point
    2, nothing already on disk becomes invalid). `acknowledged` keys are the
    author's own `external_ref:` entries: a link the author explicitly
    recorded as cross-vault is not a violation."""
    violations, repairs = [], []
    targets = [t for t in dict.fromkeys(_mem._mem_link_targets(body))
               if t not in set(skip_targets)]
    if not targets:
        return violations, repairs
    own = _vault_keys(mem_dir, with_aka=True)
    for t in targets:
        key = _mem._mem_link_key(t)
        if key in acknowledged:
            continue
        if key in own:
            if t.strip().lower().endswith('.md'):
                repairs.append({'class': R_LINK_A, 'target': t, 'resolves_to': own[key]})
            continue
        vault = _foreign_vault_of(mem_dir, key)
        if vault:
            violations.append({
                'class': V_LINK_C, 'target': t, 'vault': vault,
                'detail': (f'[[{t}]] resolves in another project\'s vault '
                           f'({vault}); a cross-project link is never traversed'),
                'remedy': (f'remove the [[link]] and record `external_ref: '
                           f'{vault}/{t}` in the frontmatter')})
        else:
            near = difflib.get_close_matches(key, list(own), n=3, cutoff=0.6)
            violations.append({
                'class': V_LINK_D, 'target': t,
                'candidates': [own[k] for k in near],
                'detail': f'[[{t}]] resolves in no vault',
                'remedy': 'fix the link to an existing note, or create the target first'})
    return violations, repairs


# ── evaluation ───────────────────────────────────────────────────────────────

def evaluate(mem_dir, *, slug, content, existing_text=None, new_slug=None):
    """Run every §10.2 class that applies to a note write.

    -> dict(violations=[...], repairs=[...], kind, block, body, meta). Never
    writes. `existing_text` is the note's current content on PATCH (None on
    POST); `new_slug` is set when PATCH renames.
    """
    kind, block, body, meta = split_frontmatter(content)
    violations, repairs = [], []
    if kind == 'missing':
        violations.append({
            'class': V_FM_MISSING,
            'detail': 'note has no frontmatter block',
            'remedy': 'start the note with a --- block carrying name and description'})
    elif kind == 'unparseable':
        violations.append({
            'class': V_FM_UNPARSEABLE,
            'detail': 'frontmatter block is unclosed or has a line that is not "key: value"',
            'remedy': 'close the --- fence and keep every top-level line as key: value'})
    else:
        for f in REQUIRED_FIELDS:
            if not str(meta.get(f) or '').strip():
                violations.append({
                    'class': V_FIELD, 'field': f,
                    'detail': f'required frontmatter field {f!r} is missing or empty',
                    'remedy': f'add `{f}:` to the frontmatter'})

    # Links are read from the body when the block parsed, else the whole text.
    link_text = body if kind == 'ok' else _normalise(content)
    skip = (_mem._mem_link_targets(_split_body(existing_text))
            if existing_text is not None else ())
    acknowledged = {_mem._mem_link_key(re.split(r'[/:]', v)[-1])
                    for v in _list_values(meta.get('external_ref'))}
    lv, lr = check_links(mem_dir, link_text, skip_targets=skip,
                         acknowledged=acknowledged)
    violations += lv
    repairs += lr

    if new_slug and _mem._mem_link_key(new_slug) != _mem._mem_link_key(slug):
        old_key = _mem._mem_link_key(slug)
        akas = {_mem._mem_link_key(a) for a in _list_values(meta.get('aka'))}
        if old_key not in akas:
            violations.append({
                'class': V_LINK_B, 'old_stem': slug, 'new_stem': new_slug,
                'detail': (f'renamed {slug} -> {new_slug} without recording the '
                           'old stem in aka:; every link to the old stem now dangles'),
                'remedy': f'add `aka: {slug}` to the frontmatter'})
    return {'violations': violations, 'repairs': repairs, 'kind': kind,
            'block': block, 'body': body, 'meta': meta}


def _split_body(text):
    kind, _block, body, _meta = split_frontmatter(text or '')
    return body if kind == 'ok' else _normalise(text or '')


# ── composition (server stamps) ──────────────────────────────────────────────

def compose(ev, *, content, origin, actor, note_type, existing_text=None):
    """The file text to write: the author's frontmatter with `origin`,
    `generated` and `verified` removed and re-stamped by the server, `type`
    defaulted when absent. On PATCH the existing `verified:` entries are
    preserved verbatim and the existing `generated:` stamp (who minted it)
    is kept — only `origin` is re-derived."""
    prior_verified, prior_generated = [], []
    if existing_text is not None:
        ekind, eblock, _eb, _em = split_frontmatter(existing_text)
        if ekind == 'ok':
            _k, popped = _pop_top_level_keys(eblock.splitlines(),
                                            ('generated', 'verified'))
            prior_generated = popped.get('generated', [])
            prior_verified = popped.get('verified', [])

    if ev['kind'] == 'ok':
        lines, _ = _pop_top_level_keys(ev['block'].splitlines(), _SERVER_KEYS)
        body = ev['body']
        if not _has_type(ev['meta']):
            lines += ['metadata:', f'  type: {note_type}']
    else:   # nothing parseable to splice into: a stamp-only block, text verbatim
        lines = ['metadata:', f'  type: {note_type}']
        body = _normalise(content)

    lines.append(f'origin: {origin}')
    lines += prior_generated or ['generated:', f"  by: {actor}", f'  at: {now_iso()}']
    lines += prior_verified
    return '---\n' + '\n'.join(lines) + '\n---\n' + body.strip('\n') + '\n'


def merged_origin(caller_origin, existing_text):
    """Conservative OR (learning-safety rail 2): an update can taint a note
    but never launder one. An unattended prior origin survives an attended
    edit; legacy/blank/interactive takes the caller's stamp."""
    if caller_origin == 'unattended':
        return 'unattended'
    kind, _b, _body, meta = split_frontmatter(existing_text or '')
    prior = str(meta.get('origin') or '').strip() if kind == 'ok' else ''
    return 'unattended' if prior == 'unattended' else caller_origin


# ── the write ────────────────────────────────────────────────────────────────

def mem_dir_for(project):
    """The project's own vault dir. Refuses the shared fallback dir
    (`MEMORY_DIR`, used when a project has no path): it holds every such
    project's MEMORY file side by side, so a note written there is in
    another project's dir."""
    mem_dir = _mem._get_memory_path(project).parent
    shared = _mem.MEMORY_DIR
    if shared is not None and mem_dir.resolve() == Path(shared).resolve():
        raise SlugRefused('project has no per-project memory vault (its memory '
                          'file lives in the shared fallback dir)')
    return mem_dir


def identity_check(mem_dir, stem, *, ignore_stem=None):
    """-> (exact, violation|None). `exact`: a file already holds this stem —
    always refused (a report-mode overwrite would destroy a note). The
    violation is the mode-dependent half: a DIFFERENT file whose link key is
    identical (`arch-x` vs `arch_x`), which makes `[[arch-x]]` ambiguous."""
    if (mem_dir / f'{stem}.md').exists():
        return True, None
    key = _mem._mem_link_key(stem)
    for f in mem_dir.glob('*.md'):
        if f.name in _NON_NOTE_FILES or f.stem == ignore_stem:
            continue
        if _mem._mem_link_key(f.stem) == key:
            return False, {
                'class': V_DUP, 'collides_with': f.stem,
                'detail': (f'{stem} and {f.stem} are the same note to every '
                           '[[wikilink]] (links ignore case and punctuation)'),
                'remedy': 'choose a slug that differs in more than punctuation'}
    return False, None


def atomic_write(project_id, mem_dir, name, text, *, must_exist=None):
    """Write under the per-project topic lock (same lock write_topic_note /
    resolve_mint use). `must_exist` re-checks inside the lock: False = the
    file must NOT exist (POST), True = it must (PATCH). Returns False when the
    re-check fails — a racing writer won."""
    path = mem_dir / name
    with _mem._get_mem_write_lock(f'topic:{project_id}'):
        if must_exist is False and path.exists():
            return False
        if must_exist is True and not path.is_file():
            return False
        mem_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, text)
    return True


def rename_file(project_id, mem_dir, old_name, new_name, text):
    """Write `new_name`, then remove `old_name`, in one locked section."""
    old, new = mem_dir / old_name, mem_dir / new_name
    with _mem._get_mem_write_lock(f'topic:{project_id}'):
        if new.exists() or not old.is_file():
            return False
        _atomic_write_text(new, text)
        old.unlink()
    return True


# ── counter ──────────────────────────────────────────────────────────────────

def _log_path(project_id):
    """Sibling to DATA_DIR, never inside it (CLAUDE.md's DATA_DIR-pollution
    rule) — same placement as `negation_waiver_log`."""
    safe = ''.join(c for c in str(project_id or 'unknown')
                   if c.isalnum() or c in ('-', '_')) or 'unknown'
    return _mem.DATA_DIR.parent / 'memory_gate_log' / f'{safe}.jsonl'


def record(project_id, *, op, slug, mode, origin, result, violations=(),
           repairs=(), refusal=''):
    """Append one row per request. `result`: 'written' | 'rejected' |
    'refused' (a rail, not a gate class). `would_reject` is the class list in
    report mode — the numerator of the false-rejection rate. Never raises."""
    row = {
        'ts': now_iso(), 'project_id': project_id, 'op': op, 'slug': slug,
        'mode': mode, 'origin': origin, 'result': result,
        'classes': [v['class'] for v in violations],
        'repaired': [r['class'] for r in repairs],
        'violations': list(violations),
    }
    if refusal:
        row['refusal'] = refusal
    if violations or repairs or result != 'written':
        _log(f'[memory-gate] {op} {slug} mode={mode} result={result} '
             f'classes={row["classes"]} repaired={row["repaired"]}'
             f'{" refusal=" + refusal if refusal else ""}')
    try:
        p = _log_path(project_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    except Exception as e:
        _log(f'[memory-gate] counter write failed for {project_id}: {e}')
    return row


def gate_counts(project_id):
    """-> the counter the step-9 harness reads. Best-effort, never raises.

    requests            every logged request
    clean               requests with no violation and no refusal
    would_reject        report-mode requests that carried >=1 violation
    rejected            enforce-mode refusals
    refused             rail refusals (slug / clobber / cross-project)
    by_class            violation count per §10.2 class
    repaired            class-A repairs (never violations)
    false_rejection_rate would_reject / requests, or None with no requests
    """
    out = {'project_id': project_id, 'requests': 0, 'clean': 0,
           'would_reject': 0, 'rejected': 0, 'refused': 0,
           'by_class': {}, 'repaired': 0, 'false_rejection_rate': None}
    try:
        p = _log_path(project_id)
        if not p.is_file():
            return out
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                out['requests'] += 1
                classes = row.get('classes') or []
                out['repaired'] += len(row.get('repaired') or [])
                for c in classes:
                    out['by_class'][c] = out['by_class'].get(c, 0) + 1
                res = row.get('result')
                if res == 'rejected':
                    out['rejected'] += 1
                elif res == 'refused':
                    out['refused'] += 1
                elif classes:
                    out['would_reject'] += 1
                else:
                    out['clean'] += 1
    except Exception as e:
        _log(f'[memory-gate] counter read failed for {project_id}: {e}')
    if out['requests']:
        out['false_rejection_rate'] = round(out['would_reject'] / out['requests'], 4)
    return out
