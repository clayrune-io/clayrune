"""The semver subset npm dependency ranges use (docs/DESK_SERVICE_PROFILES_SPEC.md, section 6.2,
slice U2b). Pure functions: no I/O, no registry, no npm. `custom_npm_closure` calls it to turn
a package's declared `"dep": "^1.2.3"` into ONE exact version, on the server, before anything
is approved; a range never reaches the launch line or the operation.

Follows node-semver's rules for what it supports: exact versions, `x`/`*` ranges and partial
versions, `^`, `~`, `>`, `>=`, `<`, `<=`, `=`, hyphen ranges, space (AND) and `||` (OR), and its
prerelease rule (a prerelease version matches only a comparator set that names a prerelease of
the same major.minor.patch). Everything else (a git or file address, `npm:` aliases, `workspace:`)
is NOT a range: `parse_range` raises `RangeError`, and the caller refuses the dependency.
"""
from __future__ import annotations

import re
from functools import cmp_to_key

MAX_RANGE = 200
_NUM = r'(?:0|[1-9]\d*)'
_VERSION_RE = re.compile(rf'^v?({_NUM})\.({_NUM})\.({_NUM})(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$')
_PARTIAL_RE = re.compile(rf'^v?({_NUM}|[xX*])(?:\.({_NUM}|[xX*])(?:\.({_NUM}|[xX*])(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?)?)?$')
_TAG_RE = re.compile(r'^[A-Za-z][A-Za-z0-9._-]{0,63}$')
_OPS = ('>=', '<=', '>', '<', '=', '^', '~>', '~')


class RangeError(ValueError):
    """The text is not an npm version range this module understands."""


def parse_version(text: str):
    """`(major, minor, patch, prerelease_tuple)` for an exact version, or None. Build metadata is dropped."""
    m = _VERSION_RE.match(text.strip()) if isinstance(text, str) else None
    if not m:
        return None
    pre = tuple(int(p) if p.isdigit() else p for p in m.group(4).split('.')) if m.group(4) else ()
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), pre)


def _cmp_ident(a, b) -> int:
    if isinstance(a, int) and isinstance(b, int):
        return (a > b) - (a < b)
    if isinstance(a, int):
        return -1                                       # numeric identifiers sort before alphanumeric ones
    if isinstance(b, int):
        return 1
    return (a > b) - (a < b)


def compare(a, b) -> int:
    """-1, 0 or 1 for two parsed versions (a prerelease sorts before its release)."""
    for x, y in zip(a[:3], b[:3]):
        if x != y:
            return -1 if x < y else 1
    pa, pb = a[3], b[3]
    if not pa and not pb:
        return 0
    if not pa:
        return 1
    if not pb:
        return -1
    for x, y in zip(pa, pb):
        c = _cmp_ident(x, y)
        if c:
            return c
    return (len(pa) > len(pb)) - (len(pa) < len(pb))


sort_key = cmp_to_key(compare)


def _partial(text: str):
    """`(major, minor, patch, pre)` with None for a missing or wildcard part."""
    m = _PARTIAL_RE.match(text)
    if not m:
        raise RangeError(f'not a version: {text[:40]}')
    parts = [None if (g is None or g in 'xX*') else int(g) for g in m.groups()[:3]]
    for i in range(3):                                  # `1.x.3` is not a range: a wildcard ends the version
        if parts[i] is None and any(p is not None for p in parts[i + 1:]):
            raise RangeError(f'not a version: {text[:40]}')
    pre = tuple(int(p) if p.isdigit() else p for p in m.group(4).split('.')) if m.group(4) else ()
    return parts[0], parts[1], parts[2], pre


def _v(major, minor, patch, pre=()):
    return (major, minor, patch, pre)


_LOW = (0, 0, 0, ())


def _lt0(major, minor, patch):
    """`<major.minor.patch-0`: below every prerelease of that version."""
    return ('<', (major, minor, patch, (0,)))


def _expand(op: str, text: str) -> list:
    """The comparators `[(operator, version)]` for one operator and partial version."""
    major, minor, patch, pre = _partial(text)
    if major is None:                                   # `*`, `x`, ``
        return [('>=', _LOW)] if op in ('', '=', '^', '~', '~>', '>=', '<=') else [('<', _LOW)] if op == '<' else [('>', (10 ** 9, 0, 0, ()))]
    if op in ('', '='):
        if patch is not None:
            return [('=', _v(major, minor, patch, pre))]
        if minor is not None:
            return [('>=', _v(major, minor, 0)), _lt0(major, minor + 1, 0)]
        return [('>=', _v(major, 0, 0)), _lt0(major + 1, 0, 0)]
    if op in ('~', '~>'):
        if minor is None:
            return [('>=', _v(major, 0, 0)), _lt0(major + 1, 0, 0)]
        if patch is None:
            return [('>=', _v(major, minor, 0)), _lt0(major, minor + 1, 0)]
        return [('>=', _v(major, minor, patch, pre)), _lt0(major, minor + 1, 0)]
    if op == '^':
        if minor is None:
            return [('>=', _v(major, 0, 0)), _lt0(major + 1, 0, 0)]
        if patch is None:
            low = _v(major, minor, 0)
            return [('>=', low), _lt0(major + 1, 0, 0) if major else _lt0(0, minor + 1, 0)]
        low = _v(major, minor, patch, pre)
        if major:
            return [('>=', low), _lt0(major + 1, 0, 0)]
        if minor:
            return [('>=', low), _lt0(0, minor + 1, 0)]
        return [('>=', low), _lt0(0, 0, patch + 1)]
    if op == '>=':
        return [('>=', _v(major, minor or 0, patch or 0, pre))]
    if op == '>':
        if patch is not None:
            return [('>', _v(major, minor, patch, pre))]
        if minor is not None:
            return [('>=', _v(major, minor + 1, 0))]
        return [('>=', _v(major + 1, 0, 0))]
    if op == '<':
        if patch is not None and pre:
            return [('<', _v(major, minor, patch, pre))]
        return [_lt0(major, minor or 0, patch or 0)]
    if op == '<=':
        if patch is not None:
            return [('<=', _v(major, minor, patch, pre))]
        if minor is not None:
            return [_lt0(major, minor + 1, 0)]
        return [_lt0(major + 1, 0, 0)]
    raise RangeError('unsupported operator')


def _set(text: str) -> list:
    """The comparators of one `||` alternative."""
    text = re.sub(r'\s+', ' ', text.strip())
    if not text:
        return [('>=', _LOW)]
    hy = re.match(r'^(\S+) - (\S+)$', text)
    if hy:
        lo, hi = _partial(hy.group(1)), _partial(hy.group(2))
        out = [] if lo[0] is None else [('>=', _v(lo[0], lo[1] or 0, lo[2] or 0, lo[3]))]
        if hi[0] is not None:
            if hi[2] is not None:
                out.append(('<=', _v(*hi)))
            elif hi[1] is not None:
                out.append(_lt0(hi[0], hi[1] + 1, 0))
            else:
                out.append(_lt0(hi[0] + 1, 0, 0))
        return out or [('>=', _LOW)]
    text = re.sub(r'(>=|<=|>|<|=|\^|~>|~)\s+', r'\1', text)
    out: list = []
    for tok in text.split(' '):
        op = next((o for o in _OPS if tok.startswith(o)), '')
        out.extend(_expand(op, tok[len(op):]))
    return out


def parse_range(text: str) -> list:
    """A list of comparator sets (OR of ANDs) for an npm range, or RangeError."""
    if not isinstance(text, str) or len(text) > MAX_RANGE or '\x00' in text:
        raise RangeError('not a version range')
    if re.search(r'[:/@\\#]', text) or text.lower().startswith(('git', 'file', 'link', 'npm', 'http', 'workspace')):
        raise RangeError('not a version range')
    sets = [_set(part) for part in text.split('||')]
    if len(sets) > 20:
        raise RangeError('not a version range')
    return sets


def satisfies(version: str, rng) -> bool:
    """True when the exact `version` is inside `rng` (a string or a `parse_range` result)."""
    v = parse_version(version)
    if v is None:
        return False
    for comps in (parse_range(rng) if isinstance(rng, str) else rng):
        if all(_ok(v, op, ver) for op, ver in comps):
            if not v[3] or any(ver[3] and ver[:3] == v[:3] for _op, ver in comps):
                return True
    return False


def _ok(v, op: str, ver) -> bool:
    c = compare(v, ver)
    return {'=': c == 0, '>': c > 0, '>=': c >= 0, '<': c < 0, '<=': c <= 0}[op]


def max_satisfying(versions, rng, *, avoid=frozenset()):
    """The highest of `versions` (exact strings) inside `rng`; a version in `avoid` (for example one
    the registry marks deprecated) is chosen only when nothing else fits. None when none fits."""
    parsed = parse_range(rng) if isinstance(rng, str) else rng
    ok = sorted((v for v in versions if parse_version(v) is not None and satisfies(v, parsed)),
                key=lambda s: sort_key(parse_version(s)))
    preferred = [v for v in ok if v not in avoid]
    pool = preferred or ok
    return pool[-1] if pool else None


def is_tag(text) -> bool:
    """A dist-tag name such as `latest` (letters first, so never a range)."""
    return isinstance(text, str) and bool(_TAG_RE.match(text)) and parse_version(text) is None \
        and not re.match(r'^[xX]$', text)
