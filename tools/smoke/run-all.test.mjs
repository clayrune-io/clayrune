// Tests for run-all.mjs (MC-1179 T8-2): skip list honoured, shards partition the
// set, a failing smoke makes exit 1, a hanging smoke is ended at the timeout and
// reported as timeout, along with the grandchild it spawned.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const RUNNER = fileURLToPath(new URL('./run-all.mjs', import.meta.url));

function makeDir(files, skips) {
  const dir = mkdtempSync(join(tmpdir(), 'runall-'));
  for (const [name, body] of Object.entries(files)) writeFileSync(join(dir, name), body);
  if (skips) writeFileSync(join(dir, 'ci-skip.json'), JSON.stringify(skips));
  return dir;
}
const run = (dir, ...args) =>
  spawnSync(process.execPath, [RUNNER, '--dir', dir, ...args], { encoding: 'utf8', timeout: 60000 });

test('every *.mjs is run except the skip list and the runner itself; green exits 0', () => {
  const dir = makeDir(
    { 'a.mjs': 'console.log("a ok")', 'b.mjs': 'console.log("b ok")', 'live.mjs': 'process.exit(1)', 'run-all.mjs': 'process.exit(1)' },
    { 'live.mjs': 'talks to the live port' });
  try {
    const r = run(dir);
    assert.equal(r.status, 0, r.stdout + r.stderr);
    assert.match(r.stdout, /PASS\s+a\.mjs/);
    assert.match(r.stdout, /PASS\s+b\.mjs/);
    assert.doesNotMatch(r.stdout, /live\.mjs/);
    assert.match(r.stdout, /ran 2: 2 pass, 0 fail, 0 timeout/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('shards partition the set: disjoint, complete, sorted-name index modulo N', () => {
  const names = ['e', 'c', 'a', 'd', 'b', 'f', 'g'].map((n) => `${n}.mjs`);
  const dir = makeDir(Object.fromEntries(names.map((n) => [n, ''])), { 'f.mjs': 'skipped' });
  try {
    const list = (...a) => run(dir, '--list', ...a).stdout.split('\n').filter(Boolean);
    const whole = list();
    assert.deepEqual(whole, ['a.mjs', 'b.mjs', 'c.mjs', 'd.mjs', 'e.mjs', 'g.mjs']);
    const shards = [1, 2, 3].map((i) => list('--shard', `${i}/3`));
    assert.deepEqual(shards, [['a.mjs', 'd.mjs'], ['b.mjs', 'e.mjs'], ['c.mjs', 'g.mjs']]);
    assert.deepEqual(shards.flat().sort(), whole);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('a failing smoke makes exit 1 and its last 15 lines are printed', () => {
  const body = 'for (let i = 1; i <= 40; i++) console.log("line " + i); console.log("boom"); process.exit(3);';
  const dir = makeDir({ 'ok.mjs': '', 'bad.mjs': body });
  try {
    const r = run(dir);
    assert.equal(r.status, 1);
    assert.match(r.stdout, /FAIL\s+bad\.mjs/);
    assert.match(r.stdout, /PASS\s+ok\.mjs/);
    assert.match(r.stdout, /1 RED/);
    assert.match(r.stdout, /--- bad\.mjs \(exit 3\) ---/);
    const tail = r.stdout.split('--- bad.mjs (exit 3) ---')[1];
    assert.match(tail, /line 40/);
    assert.match(tail, /boom/);
    assert.doesNotMatch(tail, /line 25\b/); // 15 lines: 27..40 plus "boom"
    assert.match(tail, /line 27\b/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('a hanging smoke is ended at the timeout, reported as timeout, and its child dies too', () => {
  const dir = makeDir({ 'good.mjs': '', 'hang.mjs': '' });
  const pidFile = join(dir, 'grandchild.pid');
  writeFileSync(join(dir, 'hang.mjs'), `
    import { spawn } from 'node:child_process';
    import { writeFileSync } from 'node:fs';
    // Windows: only a detached child outlives a parent-only kill, so that is the one that proves /T.
    const g = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { stdio: 'ignore', detached: process.platform === 'win32' });
    writeFileSync(${JSON.stringify(pidFile)}, String(g.pid));
    console.log('hanging');
    setInterval(() => {}, 1000);`);
  try {
    const t0 = Date.now();
    const r = run(dir, '--timeout', '2');
    assert.ok(Date.now() - t0 < 30000, 'runner must not wait on the hung smoke');
    assert.equal(r.status, 1, r.stdout + r.stderr);
    assert.match(r.stdout, /TIMEOUT\s+hang\.mjs/);
    assert.match(r.stdout, /PASS\s+good\.mjs/);
    assert.match(r.stdout, /--- hang\.mjs \(timeout after 2s\) ---/);
    assert.match(r.stdout, /hanging/);
    assert.ok(existsSync(pidFile), 'the smoke spawned a grandchild');
    const pid = Number(readFileSync(pidFile, 'utf8'));
    let alive = true;
    for (let i = 0; i < 20 && alive; i++) {
      try { process.kill(pid, 0); spawnSync(process.execPath, ['-e', 'setTimeout(()=>{},100)']); } catch { alive = false; }
    }
    assert.equal(alive, false, `grandchild ${pid} was left running`);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('a skip entry without a reason is a usage error (exit 2), not a silent skip', () => {
  const dir = makeDir({ 'a.mjs': '' }, { 'a.mjs': '' });
  try {
    const r = run(dir);
    assert.equal(r.status, 2);
    assert.match(r.stderr, /needs a one-line reason|no reason/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
