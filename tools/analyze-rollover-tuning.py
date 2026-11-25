"""Measure rollover inputs from local Clayrune logs and Claude main transcripts.

Standard library only. Emits Markdown to stdout, with anonymous character labels
unless --local-labels is set. Never prints prompts, account IDs or session IDs.
"""
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import json
from math import ceil, sqrt
import os
from pathlib import Path
import re
from statistics import mean, median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mc.rollover_model_table import MODEL_TABLE


UNKNOWN = 'unknown'
ALL = '*'
BASE = 200_000
ROOM = 60_000
KEYS = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
PRIOR = re.compile(r'^=== Prior conversation, started on [\w-]+ \(session ([\w-]+)\)')


def instant(s):
    dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if not dt.tzinfo:
        raise ValueError('Expected a timestamp with a time zone')
    return dt.timestamp()


def role(v):
    if isinstance(v, dict) and v.get('name'):
        return f"{v.get('scope') or 'global'}:{v['name']}"
    return v if isinstance(v, str) and v else UNKNOWN


def unique(values):
    known = set(values) - {None, '', UNKNOWN}
    return known.pop() if len(known) == 1 else UNKNOWN


def pair(values):
    if not values:
        return '—'
    ordered = sorted(values)
    return f'{median(ordered):,.0f} / {ordered[ceil(.9 * len(ordered)) - 1]:,}'


def table(headers, rows):
    print('| ' + ' | '.join(headers) + ' |')
    print('| ' + ' | '.join('---' for _ in headers) + ' |')
    for row in rows:
        print('| ' + ' | '.join(map(str, row)) + ' |')
    print()


@dataclass
class Measures:
    floors: list = field(default_factory=list)
    deltas: list = field(default_factory=list)
    turns: list = field(default_factory=list)
    floor_chats: set = field(default_factory=set)
    growth_chats: Counter = field(default_factory=Counter)
    calls: int = 0
    rolls: int = 0
    rapid: int = 0
    previous: int = 0
    pending: int = 0
    pending_known: int = 0
    intervals: dict = field(default_factory=lambda: defaultdict(list))


@dataclass
class Call:
    ts: float
    size: int
    model: str
    effort: str
    turn: int
    epoch: int
    effort_source: str


@dataclass
class Segment:
    calls: list = field(default_factory=list)
    prior: str = ''
    rooted: bool = False


def main_transcript(path, native, end, audit):
    """Only parent-conversation usage. Duplicate content blocks share a message ID."""
    segment = Segment()
    requests = {}
    seen = set()
    turn = epoch = 0
    root_seen = False
    for raw in path.open('rb'):
        if not raw.strip():
            continue
        try:
            e = json.loads(raw)
        except ValueError:
            audit['bad transcript lines'] += 1
            # Resume at the next prompt. Do not infer a delta across a hole.
            epoch += 1
            continue
        if e.get('sessionId') != native or e.get('isSidechain') or e.get('parentToolUseID'):
            continue
        typ = e.get('type')
        if typ == 'api-request':
            requests[e.get('id')] = (e.get('params', {}).get('output_config') or {}).get('effort')
            continue
        if 'parentUuid' in e and e['parentUuid'] is None:
            root_seen = True
            epoch += 1
        if typ == 'system' and e.get('subtype') == 'compact_boundary':
            epoch += 1
        m = e.get('message') or {}
        if not isinstance(m, dict):
            continue
        content = m.get('content')
        if typ == 'user' and not e.get('isMeta'):
            prompt = isinstance(content, str) or (
                isinstance(content, list) and any(b.get('type') in ('text', 'image')
                    for b in content if isinstance(b, dict))
                and not any(b.get('type') == 'tool_result' for b in content if isinstance(b, dict)))
            if prompt:
                turn += 1
                if turn == 1 and not segment.calls:
                    segment.rooted = root_seen
                    match = PRIOR.match(content) if isinstance(content, str) else None
                    segment.prior = match.group(1) if match else ''
        if typ != 'assistant' or not m.get('id') or m['id'] in seen or not e.get('timestamp'):
            continue
        u = m.get('usage') or {}
        size = sum(u.get(k) or 0 for k in KEYS)
        model = m.get('model')
        if size <= 0 or not model or model.startswith('<'):
            continue
        seen.add(m['id'])
        ts = instant(e['timestamp'])
        if ts >= end:
            continue
        effort, source = next(((value, field_name) for field_name, value in (
            ('request effort', requests.get(e.get('requestRef'))),
            ('per-turn effort', e.get('perTurnEffort')), ('assistant effort', e.get('effort')))
            if value), (UNKNOWN, 'effort missing'))
        model = re.sub(r'-\d{8}$', '', model.removesuffix('[1m]'))
        segment.calls.append(Call(ts, size, model, effort, turn, epoch, source))
    return segment


def groups(character, model, effort):
    return ((ALL, ALL, ALL), (ALL, model, ALL), (ALL, model, effort), (character, model, effort))


def comparable(p, c, start, pool_effort):
    return (p and start <= p.ts <= c.ts and p.epoch == c.epoch
            and p.model == c.model and (pool_effort or p.effort == c.effort))


def record_usage(stats, segment, char, chat, start):
    prev = first = None
    mixed_effort = mixed_model = False
    for i, c in enumerate(segment.calls):
        for key in groups(char, c.model, c.effort):
            if c.ts < start:
                continue
            s = stats[key]
            s.calls += 1
            if i == 0 and segment.rooted:
                s.floors.append(c.size)
                if chat != UNKNOWN:
                    s.floor_chats.add(chat)
            if comparable(prev, c, start, key[2] == ALL):
                s.deltas.append(c.size - prev.size)
                s.growth_chats[chat] += 1
            if (not mixed_model and (key[2] == ALL or not mixed_effort)
                    and comparable(first, c, start, key[2] == ALL) and prev.turn != c.turn
                    and first.turn + 1 == c.turn and first.turn):
                s.turns.append(c.size - first.size)
        if not first or c.turn != first.turn or c.epoch != first.epoch:
            first = c
            mixed_effort = mixed_model = False
        else:
            mixed_effort |= c.effort != first.effort
            mixed_model |= c.model != first.model
        prev = c


def cost_fit(stats, model):
    prices = MODEL_TABLE.get(model, {})
    if not (prices.get('write_read_ratio') and stats.floors and stats.deltas):
        return None
    f, g = median(stats.floors), mean(stats.deltas)
    if g <= 0:
        return None
    # Relative prices identify the optimum and percentage changes even when
    # the legacy table has no dollars. Never label relative units as dollars.
    r = prices['cache_read_price'] or 1.0
    w = r * prices['write_read_ratio']
    optimum = f + sqrt(2 * f * g * w / r)
    def cost(t):
        if t <= f:
            return float('inf')
        return (r * (f + (t - f) / 2) + w * f * g / (t - f)) / 1_000_000
    return optimum, cost


def candidate(s, model):
    fit = cost_fit(s, model)
    # Coverage rule is a proposed decision rule, not a statistical confidence interval.
    if not fit or len(s.floor_chats) < 5 or len(s.floors) < 10 or len(s.deltas) < 100:
        return None
    if max(s.growth_chats.values()) > len(s.deltas) / 2:
        return None
    optimum, cost = fit
    f90 = sorted(s.floors)[ceil(.9 * len(s.floors)) - 1]
    # Spend up to 2% above the minimum for up to 30k more continuity.
    grid = round(optimum / 10000) * 10000
    choices = [grid + d for d in (0, 10000, 20000, 30000)
               if grid + d <= optimum + 30000 and cost(grid + d) <= 1.02 * cost(optimum)]
    minimum = f90 + ROOM
    target = max([grid, *choices, ceil(minimum / 10000) * 10000])
    prices = MODEL_TABLE[model]
    if minimum >= prices['window_tokens']:
        return None
    cap = min(prices['window_tokens'] // 2, prices.get('price_step_tokens') or target)
    return min(target, max(cap, minimum))


def analyze(args):
    start, end = instant(args.since), instant(args.until)
    if start >= end:
        raise ValueError('Expected --since < --until')
    indexed = defaultdict(list)
    rolls = []
    audit = Counter()
    runs = Counter()
    run_fields = Counter()
    requested = defaultdict(set)
    roll_files = sorted((args.data / 'midturn_rollover_log').glob('*.jsonl'))
    run_files = sorted((args.data / 'projects').glob('*_agent_log.json'))
    if not roll_files or not run_files or not args.transcripts.is_dir():
        raise ValueError('Expected rollover logs, agent logs and a transcript directory')
    for path in run_files:
        project = path.name.removesuffix('_agent_log.json')
        for z in json.loads(path.read_bytes()):
            ts = z.get('ts')
            if not ts or not start <= instant(ts) < end:
                continue
            runs[z.get('provider') or UNKNOWN] += 1
            for field_name in ('requested_effort', 'cost_usd', 'num_turns'):
                run_fields[field_name] += z.get(field_name) is not None
            if z.get('provider') not in (None, '', 'claude'):
                continue
            if z.get('requested_effort') and z.get('effort_support') == 'supported':
                requested[project, z.get('session_id'), z.get('claude_session_id')].add(z['requested_effort'])
            for native in {z.get('claude_session_id'), *(z.get('claude_session_ids') or [])} - {None, ''}:
                indexed[native].append((project, z.get('session_id'), role(z.get('character'))))
    for path in roll_files:
        for line in path.open('rb'):
            if line.strip():
                z = json.loads(line)
                if instant(z['ts']) >= end or z.get('event'):
                    continue
                rolls.append(z)
                if instant(z['ts']) >= start and z.get('claude_session_id'):
                    indexed[z['claude_session_id']].append((z['project_id'], z['session_id'], role(z.get('character'))))
    paths = defaultdict(list)
    for d in os.scandir(args.transcripts):
        if d.is_dir():
            for p in os.scandir(d.path):
                if p.name.endswith('.jsonl') and p.name[:-6] in indexed:
                    paths[p.name[:-6]].append(Path(p.path))
    stats = defaultdict(Measures)
    segments = {}
    owners = {}
    for native, records in indexed.items():
        char = unique(r[2] for r in records)
        chat = unique((r[0], r[1]) for r in records if r[1])
        owners[native] = char
        if len(paths[native]) != 1:
            audit['ambiguous paths' if paths[native] else 'missing paths'] += 1
            continue
        seg = main_transcript(paths[native][0], native, end, audit)
        segments[native] = seg
        record_usage(stats, seg, char, chat, start)
    last = {}
    anomalies = []
    logged = []
    for z in sorted(rolls, key=lambda row: row['ts']):
        chat = z['project_id'], z['session_id']
        old = last.get(chat)
        last[chat] = z
        if instant(z['ts']) < start:
            continue
        logged.append(z)
        native = z.get('claude_session_id', '')
        seg = segments.get(native, Segment())
        # An ID sampled during respawn can already point at the incoming process.
        # Require the trigger's actual token count AND a timestamp at/before the roll.
        hits = [i for i, c in enumerate(seg.calls) if c.size == z.get('context_tokens')
                and c.ts < instant(z['ts']) + 1]
        match = hits[0] if len(hits) == 1 else None
        c = seg.calls[match] if match is not None else None
        if c:
            audit['matched triggers'] += 1
            audit[c.effort_source + ' at trigger'] += 1
        requests_for_native = requested.get((*chat, native), set())
        if len(requests_for_native) == 1:
            audit['rolls with direct run requested effort'] += 1
            if c and c.effort != UNKNOWN and c.effort not in requests_for_native:
                audit['request setting differs from call'] += 1
        if seg.rooted and seg.calls and z.get('fresh_floor_tokens') is not None:
            audit['logged floors compared to native start'] += 1
            if seg.calls[0].size != z['fresh_floor_tokens']:
                audit['logged floors differing from native start'] += 1
        char = role(z.get('character'))
        if char == UNKNOWN:
            char = owners.get(native, UNKNOWN)
        keys = groups(char, c.model if c else z.get('model') or UNKNOWN, c.effort if c else UNKNOWN)
        for key in keys:
            s = stats[key]
            s.rolls += 1
            if old:
                s.previous += 1
                s.rapid += instant(z['ts']) - instant(old['ts']) < 300
            if z.get('unanswered_msgs') is not None:
                s.pending_known += 1
                s.pending += z['unanswered_msgs'] > 0
            if (c and old and seg.rooted and seg.prior == old['claude_session_id']
                    and instant(old['ts']) - 1 <= seg.calls[0].ts and seg.calls[0].turn == 1):
                observed = seg.calls[:match + 1]
                s.intervals[z['threshold']].append((len(observed), len({v.turn for v in observed})))
        floor = z.get('fresh_floor_tokens')
        if (floor or 0) > BASE and z.get('model', '') == 'claude-fable-5-2':
            positions = [i+1 for i, v in enumerate(seg.calls) if v.size == floor]
            incoming = [v.calls[0].size for v in segments.values() if v.calls and v.rooted
                        and v.prior == native and v.calls[0].ts >= instant(z['ts']) - 1]
            anomalies.append((z['ts'][:10], floor, seg.calls[0].size if seg.calls else '—',
                              positions, z['context_tokens'], incoming))
    render(args, stats, audit, runs, run_fields, rolls, logged, anomalies, indexed, roll_files, run_files)


def render(args, stats, audit, runs, run_fields, rolls, logged, anomalies, indexed, roll_files, run_files):
    names = sorted({k[0] for k in stats} - {ALL, UNKNOWN})
    labels = {n: f'C{i:02}' for i, n in enumerate(names, 1)}
    def label(key):
        char, model, effort = key
        char = char if args.local_labels else labels.get(char, char)
        return ' / '.join([char, model.removeprefix('claude-'), str(effort)])
    print(f'Calibration [{args.since}, {args.until}). {len(roll_files)} roll files, '
          f'{len(run_files)} run files, {len(rolls)} historical mid-turn rolls through cutoff, '
          f'{len(logged)} in calibration. Retained run rows by provider: {dict(sorted(runs.items()))}. '
          f'Non-null run fields: {dict(run_fields)}. {len(indexed)} indexed native IDs. '
          f'Audit: {dict(sorted(audit.items()))}.\n')
    keys = sorted(stats, key=lambda k: (k[0] != ALL, k))
    fitted = {k: candidate(stats[k], k[1]) for k in keys}
    recs, metrics, risks, costs, cadence = [], [], [], [], []
    for k in keys:
        s = stats[k]
        name = label(k)
        model_key = (ALL, k[1], ALL)
        effort_key = (ALL, k[1], k[2])
        choices = [(k, 'cell' if k[0] != ALL else 'model/effort' if k[2] != ALL else 'model'),
                   (effort_key, 'model/effort'), (model_key, 'model')]
        source, value = next(((kind, fitted[p]) for p,kind in choices
                              if UNKNOWN not in p and fitted.get(p)), ('base', BASE))
        own = UNKNOWN not in k and fitted[k]
        peak = f'{100 * max(s.growth_chats.values()) / len(s.deltas):.0f}%' if s.deltas else '—'
        recs.append([name, f'{value:,}', source, len(s.floor_chats), len(s.floors), len(s.deltas), peak])
        metrics.append([name, s.calls, pair(s.floors), pair(s.deltas),
                        f'{mean(s.deltas):,.0f}' if s.deltas else '—', len(s.turns), pair(s.turns)])
        if s.rolls:
            risks.append([name, s.rolls, f'{s.rapid}/{s.previous}', f'{s.pending}/{s.pending_known}'])
        for threshold, pairs in sorted(s.intervals.items()):
            cadence.append([name, f'{threshold:,}', len(pairs), pair([p[0] for p in pairs]), pair([p[1] for p in pairs])])
        if own:
            optimum, cost = cost_fit(s, k[1])
            minimum = cost(optimum)
            changes = [100*(cost(optimum+d)/minimum-1) for d in (-30000,30000)]
            def dollars(amount):
                return f'{amount:.5f}' if MODEL_TABLE[k[1]]['cache_read_price'] else '—'
            costs.append([name, round(optimum), dollars(minimum), *(f'{n:.2f}%' for n in changes),
                          *(dollars(cost(t)) for t in (150000,200000,250000,300000,own))])
    print('### Recommended thresholds\n')
    table(['Character / model / effort','Tokens','Source','Chats with floor','Floors','Call deltas','Largest chat share'], recs)
    print('### Measured growth\n')
    table(['Character / model / effort','Calls','Floor p50 / p90','Call Δ p50 / p90','Call Δ mean','Turn deltas','Turn Δ p50 / p90'], metrics)
    print('### Cost sensitivity\n')
    table(['Character / model / effort','T*','USD/call at T*','−30k','+30k','150k USD','200k USD','250k USD','300k USD','Proposed USD'],costs)
    print('### Continuity exposure\n')
    table(['Character / model / effort','Mid-turn','<5 min / with predecessor','Pending / recorded'],risks)
    print('### Intervals with a verified handoff\n')
    table(['Character / model / effort','Actual end threshold','Intervals','Calls p50 / p90','Prompts p50 / p90'],cadence)
    print('### Fable floors over the base\n')
    table(['UTC date','Logged floor','First input','Call # matching floor','Trigger','Linked successor first input(s)'],anomalies)
    print('### Logged pin observations\n')
    pins = defaultdict(list)
    for z in logged:
        if z.get('threshold_reason') == 'pin':
            pins[(role(z.get('character')), z.get('model'), z['threshold'])].append(z)
    pin_rows = []
    for (ch, alias, t), values in sorted(pins.items()):
        room = [v['working_room_tokens'] for v in values if v.get('working_room_tokens') is not None]
        floors = [v['fresh_floor_tokens'] for v in values if v.get('fresh_floor_tokens') is not None]
        pin_rows.append([ch if args.local_labels else labels.get(ch,ch), alias, t, len(values),
                         len(floors), (min(floors),max(floors)) if floors else '—',
                         (min(room),max(room)) if room else '—'])
    table(['Character','Logged model','Pin','Rolls','Floors','Floor range','Room range'],pin_rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True, help='Installation data directory containing projects/')
    parser.add_argument('--transcripts', type=Path, default=Path.home()/'.claude'/'projects')
    parser.add_argument('--since', required=True, help='Inclusive timezone-qualified ISO timestamp')
    parser.add_argument('--until', required=True, help='Exclusive timezone-qualified ISO timestamp')
    parser.add_argument('--local-labels', action='store_true', help='Include local character names in stdout')
    analyze(parser.parse_args())
