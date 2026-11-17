#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T8) — R0 exit gate, PART A: the aggregate.
 *
 * Re-runs every other Desk smoke (R2-13: 21 of them) as a subprocess and requires every
 * one green — the regression gate a "R0 exit" needs after 7 lanes' worth of sequential
 * merges (CLAUDE.md 2026-09-10: run smokes after EACH merge, not just the last one).
 *
 * This file has NO checks of its own. Its PART B (the A1 / A12 / A15 sweeps) moved to
 * desk-v1-exit-sweeps.mjs (MC-1179 T8-2 follow-up) so it can run alone in well under
 * 120 s. Run serially, the 21 children take > 240 s, which is why the one runner
 * (run-all.mjs, which already runs each of them one by one) timed this out. Whether
 * this aggregate stays, goes on the skip list ("aggregate of smokes the runner already
 * runs"), or is deleted is left to the maintainer.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-exit.mjs
 * Exit 0 = every other Desk smoke green; 1 = one of them is not.
 */
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const __dirname = dirname(fileURLToPath(import.meta.url));

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const OTHER_SMOKES = [
  'desk-v1-harness.mjs', 'desk-v1-kit.mjs', 'desk-v1-home.mjs',
  'desk-v1-campaign.mjs', 'desk-v1-piece.mjs', 'desk-v1-map.mjs',
  'desk-v1-review.mjs',
  'desk-v1-calendar.mjs', 'desk-v1-video.mjs', 'desk-v1-conversations.mjs',
  'desk-v1-engagement.mjs',
  'desk-v1-results.mjs', 'boot-smoke.mjs',
  // R2-13: the smokes that own the map's stops and the playbook loop. Each
  // runs its own 3-tone passes (how, where, studio, retro, project/Playbook).
  'desk-v1-how.mjs', 'desk-v1-where.mjs', 'desk-v1-studio.mjs',
  'desk-v1-retro.mjs', 'desk-v1-project.mjs', 'desk-v1-connections.mjs',
  'desk-v1-journey.mjs',
];

function runOtherSmokes() {
  const results = [];
  for (const f of OTHER_SMOKES) {
    const r = spawnSync(process.execPath, [resolve(__dirname, f)], { cwd: __dirname, encoding: 'utf8' });
    const outLines = (r.stdout || '').split('\n').map((l) => l.trim()).filter(Boolean);
    const lastLine = outLines.length ? outLines[outLines.length - 1] : '(no stdout)';
    const passed = r.status === 0;
    results.push({ file: f, passed, lastLine, status: r.status });
    if (passed) ok(`${f}: exit 0 — "${lastLine}"`);
    else {
      fail(`${f}: exit ${r.status} — "${lastLine}"`);
      // A smoke can print "All checks passed." and still exit 1 on a thrown
      // error (stderr), so name the error rather than only the last stdout line.
      const errTail = (r.stderr || '').trim().split(String.fromCharCode(10)).slice(0, 8).join(' | ');
      if (errTail) console.log(`    stderr: ${errTail.slice(0, 600)}`);
    }
  }
  return results;
}

const smokeResults = runOtherSmokes();
const exitCode = (bad === 0 && smokeResults.every((r) => r.passed)) ? 0 : 1;
console.log(bad === 0 ? `
All checks passed.` : `
${bad} check(s) failed.`);
process.exit(exitCode);
