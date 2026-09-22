#!/usr/bin/env node
/**
 * Unit check for the backlog search/sort helpers (MC-955).
 *
 * WHY THIS EXISTS
 * ----------------
 * backlogItemMatchesQuery() and backlogNumComparator() are the ONE
 * implementation shared by the project Backlog tab (render-core.js) and the
 * All Backlog Items modal (cross-backlog.js) — the whole point of factoring
 * them out was that 'MC-955' / 'mc955' / '955' resolve to the same items in
 * both places. A regression here silently breaks search or sort in both
 * surfaces at once, and neither is exercised by boot-smoke.mjs (it seeds one
 * item, not enough to sort or miss a match on).
 *
 * This extracts the two functions' real source straight out of
 * static/index.html (they're plain, DOM-free functions declared in the
 * classic <script> "STORE" block — see BACKLOG_CLOSED next to them) and
 * evaluates them in isolation, so the assertions run against the actual
 * shipped implementation, not a hand-copied duplicate that could drift.
 *
 * RUN
 *   cd tools/smoke && node backlog-search-sort.mjs
 * Exit 0 = all assertions pass; 1 = a mismatch, printed.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const INDEX_HTML = resolve(__dirname, '..', '..', 'static', 'index.html');

function extractFunction(src, name) {
  const startMatch = src.match(new RegExp(`function ${name}\\(`));
  if (!startMatch) throw new Error(`function ${name} not found in static/index.html`);
  const start = startMatch.index;
  const braceStart = src.indexOf('{', start);
  let depth = 0, i = braceStart;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) break; }
  }
  return src.slice(start, i + 1);
}

const html = readFileSync(INDEX_HTML, 'utf8');
const src = extractFunction(html, 'backlogItemMatchesQuery') + '\n' +
            extractFunction(html, 'backlogNumComparator') + '\n' +
            'module.exports = { backlogItemMatchesQuery, backlogNumComparator };';

const mod = { exports: {} };
new Function('module', 'exports', src)(mod, mod.exports);
const { backlogItemMatchesQuery, backlogNumComparator } = mod.exports;

let failures = 0;
function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failures++;
    console.log(`❌ ${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  } else {
    console.log(`✓ ${label}`);
  }
}

// ── backlogItemMatchesQuery: key/num/text matching, all case-insensitive ──
const mc955 = { key: 'MC-955', num: 955, text: 'Backlog list should allow searching' };
const mc12 = { key: 'MC-12', num: 12, text: 'Unrelated item about the scheduler' };
const noKey = { key: '', num: null, text: 'An old dashboard note with no ticket' };

check('empty query matches everything', backlogItemMatchesQuery(mc955, ''), true);
check("'MC-955' matches its own item", backlogItemMatchesQuery(mc955, 'MC-955'), true);
check("'mc-955' matches (case-insensitive)", backlogItemMatchesQuery(mc955, 'mc-955'), true);
check("'955' matches via substring", backlogItemMatchesQuery(mc955, '955'), true);
check("'MC955' (no dash) matches via normalization", backlogItemMatchesQuery(mc955, 'MC955'), true);
check("'mc955' matches a DIFFERENT item's key ('MC-12') as false", backlogItemMatchesQuery(mc12, 'mc955'), false);
check('free text still matches on body', backlogItemMatchesQuery(mc955, 'searching'), true);
check('a query with no key/num hits still checks text', backlogItemMatchesQuery(noKey, 'dashboard'), true);
check('an item with no key/num does not false-match an unrelated number', backlogItemMatchesQuery(noKey, '955'), false);

// ── backlogNumComparator: ticket-number sort, missing num sorts last ──
check("mode 'default' returns null (caller keeps its own order)", backlogNumComparator('default'), null);

const items = [
  { id: 'a', num: 12 },
  { id: 'b', num: 955 },
  { id: 'c', num: null },
  { id: 'd', num: 100 },
];
const desc = [...items].sort(backlogNumComparator('num_desc')).map(i => i.id);
check("'num_desc' — highest ticket first, no-num last", desc, ['b', 'd', 'a', 'c']);
const asc = [...items].sort(backlogNumComparator('num_asc')).map(i => i.id);
check("'num_asc' — lowest ticket first, no-num last", asc, ['a', 'd', 'b', 'c']);

if (failures) {
  console.log(`\n❌ FAIL — ${failures} assertion(s) failed.`);
  process.exit(1);
} else {
  console.log(`\n✅ PASS — backlog search/sort helpers: key/num/text matching and ticket-# sort (missing-num-last) all correct.`);
}
