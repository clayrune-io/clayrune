"""The Launch panel's "what changed" list (static/js/desk-v1-reapproval.js `diff`)
must agree with the server's widening rule (mc/desk.py `bounds_widen`).

The server decides what holds the publisher; the client decides what the person
is TOLD is waiting for approval. If they disagree, either a hold is invisible (the
server holds, Launch shows nothing to approve) or Launch asks for an approval the
server never needed. This runs the same corpus through both, dimension by
dimension, so a change to one without the other fails here.

One known difference is pinned, not hidden: a LOWER `cadence.min_gap_h` is a
widening to the client (a tighter gap posts more often) and not to the server.
The client is the stricter side, so the person is asked once more than the server
requires; it never hides a hold. If the server learns the rule, the pinned case
below fails and the entry moves out of KNOWN_CLIENT_ONLY.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk  # noqa: E402

MODULE = PROJECT_ROOT / 'static' / 'js' / 'desk-v1-reapproval.js'

BASE = {
    'accounts': ['a'],
    'cadence': {'per_week': 3, 'min_gap_h': 24},
    'end': {'date': '2026-11-30', 'post_cap': 10},
    'term': {'ends': '2026-11-30'},
    'budget': {'source': 'own', 'amount': 5},
}


def variant(**changes):
    out = json.loads(json.dumps(BASE))
    for path, value in changes.items():
        top, _, sub = path.partition('__')
        if sub:
            out[top][sub] = value
        else:
            out[top] = value
    return out


CASES = {
    'unchanged': variant(),
    'account added': variant(accounts=['a', 'b']),
    'account swapped': variant(accounts=['b']),
    'account removed': variant(accounts=[]),
    'cadence raised': variant(cadence__per_week=5),
    'cadence lowered': variant(cadence__per_week=1),
    'cadence ceiling removed': variant(cadence__per_week=None),
    'end date later': variant(end__date='2026-12-31'),
    'end date earlier': variant(end__date='2026-10-01'),
    'end date removed': variant(end__date=None),
    'post cap raised': variant(end__post_cap=20),
    'post cap lowered': variant(end__post_cap=2),
    'post cap removed': variant(end__post_cap=None),
    'term later': variant(term={'ends': '2027-01-31'}),
    'term earlier': variant(term={'ends': '2026-10-31'}),
    'term removed': variant(term={}),
    'budget raised': variant(budget={'source': 'own', 'amount': 6}),
    'budget lowered': variant(budget={'source': 'own', 'amount': 1}),
    'budget from zero': {**variant(), 'budget': {'source': 'none', 'amount': 0}},
    'budget source own to project': variant(budget={'source': 'project', 'amount': 5}),
    'two dimensions': variant(accounts=['a', 'b'], budget={'source': 'own', 'amount': 9}),
    'one up one down': variant(cadence__per_week=1, end__post_cap=50),
    # the pinned difference (see the module docstring)
    'min gap lowered': variant(cadence__min_gap_h=12),
}
KNOWN_CLIENT_ONLY = {'min gap lowered'}

SERVER_DIMENSIONS = {
    'accounts': desk._accounts_widen,
    'cadence': desk._cadence_widen,
    'end': desk._end_widen,
    'term': desk._term_widen,
    'budget': desk._budget_widen,
}
CLIENT_KEY_DIMENSION = {
    'accounts': 'accounts', 'per_week': 'cadence', 'min_gap_h': 'cadence',
    'end_date': 'end', 'post_cap': 'end', 'term_ends': 'term', 'budget': 'budget',
}

RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const win = {};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), { window: win, document: {} });
process.stdout.write(JSON.stringify(cases.map((c) => win.DeskV1Reapproval.diff(c.prev, c.next).map((r) => r.key))));
"""


@pytest.fixture(scope='module')
def client_keys():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    payload = json.dumps([{'prev': BASE, 'next': nxt} for nxt in CASES.values()])
    out = subprocess.run([node, '-e', RUNNER, str(MODULE)], input=payload, capture_output=True,
                         text=True, timeout=60, check=True)
    return dict(zip(CASES, json.loads(out.stdout)))


def server_dimensions(nxt):
    return {name for name, fn in SERVER_DIMENSIONS.items() if fn(BASE, nxt)}


@pytest.mark.parametrize('name', [n for n in CASES if n not in KNOWN_CLIENT_ONLY])
def test_client_and_server_widen_the_same_dimensions(client_keys, name):
    nxt = CASES[name]
    client = {CLIENT_KEY_DIMENSION[k] for k in client_keys[name]}
    assert client == server_dimensions(nxt), name
    assert bool(client_keys[name]) == desk.bounds_widen(BASE, nxt), name


def test_the_corpus_exercises_both_outcomes(client_keys):
    assert any(client_keys[n] for n in CASES) and not all(client_keys[n] for n in CASES)
    assert client_keys['unchanged'] == []


def test_a_lower_min_gap_is_the_one_known_client_only_widening(client_keys):
    nxt = CASES['min gap lowered']
    assert client_keys['min gap lowered'] == ['min_gap_h']
    assert desk.bounds_widen(BASE, nxt) is False
