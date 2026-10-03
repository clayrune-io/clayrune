#!/usr/bin/env python3
"""Check the add-on catalogue's pins (docs/ADDON_INSTALLS_SPEC.md §2).

    python tools/addons-pin.py --check-tags   every pinned BtbN tag is a month's last build
    python tools/addons-pin.py --verify       re-download each pinned URL and re-hash it
    python tools/addons-pin.py --verify --id ffmpeg --platform windows-x86_64

BtbN keeps the last 14 daily builds and the last build of each month for two
years, so a pin on a daily tag 404s within two weeks. `--check-tags` refuses
anything that is not a month-end tag. `--verify` is the rot check: it hashes
the bytes the installer would download and exits non-zero on any mismatch or
404 (a 404 means the entry needs re-pinning). Neither writes the catalogue:
re-pinning is a reviewed commit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc.addons import catalogue  # noqa: E402

_TAG = re.compile(r'^autobuild-(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})$')
_UA = {'User-Agent': 'clayrune-addons-pin'}


def month_end_problem(tag: str, all_tags: list[str]) -> str | None:
    """None when `tag` is the latest autobuild of its month, else why not."""
    m = _TAG.match(tag)
    if not m:
        return f'{tag!r} is not an autobuild-YYYY-MM-DD-HH-MM tag'
    month = tag[len('autobuild-'):len('autobuild-') + 7]
    later = sorted(t for t in all_tags if _TAG.match(t) and t[len('autobuild-'):len('autobuild-') + 7] == month and t > tag)
    return f'{tag} is not the last build of {month} (later: {later[-1]})' if later else None


def _release_tags() -> list[str]:
    tags: list[str] = []
    for page in range(1, 8):
        req = urllib.request.Request(
            f'https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=100&page={page}', headers=_UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            batch = json.load(r)
        if not batch:
            break
        tags += [x['tag_name'] for x in batch]
    return tags


def check_tags() -> int:
    pinned = {}
    for e in catalogue.load().values():
        for key, p in (e.get('platforms') or {}).items():
            if p.get('release_tag'):
                pinned[(e['id'], key)] = p['release_tag']
    tags = _release_tags()
    bad = 0
    for (aid, key), tag in sorted(pinned.items()):
        problem = month_end_problem(tag, tags)
        print(f'{aid} [{key}] {tag}: ' + (f'REFUSED, {problem}' if problem else 'ok'))
        bad += bool(problem)
    return 1 if bad else 0


def verify(only_id: str | None, only_platform: str | None) -> int:
    bad = 0
    for e in catalogue.load().values():
        if only_id and e['id'] != only_id:
            continue
        for key, p in (e.get('platforms') or {}).items():
            if only_platform and key != only_platform:
                continue
            h, n = hashlib.sha256(), 0
            try:
                with urllib.request.urlopen(urllib.request.Request(p['url'], headers=_UA), timeout=60) as r:
                    for chunk in iter(lambda: r.read(1 << 20), b''):
                        h.update(chunk)
                        n += len(chunk)
            except Exception as ex:
                print(f'{e["id"]} [{key}]: FAILED to fetch ({ex}); the entry needs re-pinning')
                bad += 1
                continue
            ok = h.hexdigest() == p['sha256'] and n == p['download_bytes']
            print(f'{e["id"]} [{key}]: ' + ('ok' if ok else f'MISMATCH sha256={h.hexdigest()} bytes={n}'))
            bad += not ok
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--check-tags', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--id')
    ap.add_argument('--platform')
    a = ap.parse_args()
    if not (a.check_tags or a.verify):
        ap.error('pick --check-tags and/or --verify')
    rc = 0
    if a.check_tags:
        rc |= check_tags()
    if a.verify:
        rc |= verify(a.id, a.platform)
    return rc


if __name__ == '__main__':
    sys.exit(main())
