// MC-1179 W9: the Worker entry recognises the retired push routes from a closed
// list, without loading the retired enrollment/lifecycle stack on cold start.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { RETIRED_LEGACY_PATHS, isRetiredLegacyPath } from '../legacy-paths.js';
import { LIFECYCLE_PREFIX, LIFECYCLE_ROUTES, isLifecyclePath } from '../lifecycle.js';
import { ENROLLMENT_PREFIX, ENROLLMENT_ROUTES, isEnrollmentPath } from '../enrollment-http.js';

const root = new URL('../', import.meta.url);
// Static import closure of one entry file: relative specifiers only.
function closure(entry) {
  const seen = new Set(); const pending = [entry];
  while (pending.length) {
    const file = pending.pop();
    if (seen.has(file)) continue;
    seen.add(file);
    const source = readFileSync(fileURLToPath(new URL(file, root)), 'utf8');
    for (const m of source.matchAll(/^\s*(?:import|export)\b[^'"]*?from\s*['"]\.\/([^'"]+)['"]/gm)) pending.push(m[1]);
    for (const m of source.matchAll(/^\s*import\s*['"]\.\/([^'"]+)['"]/gm)) pending.push(m[1]);
  }
  return seen;
}

test('the closed retired list equals the legacy route tables', () => {
  const legacy = ['/v1/mail/send', '/v1/mail/reconcile',
    ...Object.keys(LIFECYCLE_ROUTES).map((r) => `${LIFECYCLE_PREFIX}/${r}`),
    ...ENROLLMENT_ROUTES.map((r) => `${ENROLLMENT_PREFIX}/${r}`)];
  assert.deepEqual([...RETIRED_LEGACY_PATHS].sort(), legacy.sort());
  for (const path of RETIRED_LEGACY_PATHS) assert.equal(isRetiredLegacyPath(path), true);
});

test('near misses are not retired paths, matching the legacy predicates', () => {
  for (const path of ['/v1/mail/lifecycle', '/v1/mail/lifecycle/', '/v1/mail/lifecycle/toString',
    '/v1/mail/lifecycle/revoke/', '/v1/mail/enrollment/cancel', '/v1/mail/enrollment/begin/',
    '/v1/mail/send/', '/v1/mail/manifest', '/v1/pull/send', '']) {
    assert.equal(isRetiredLegacyPath(path), false, path);
    assert.equal(isLifecyclePath(path) || isEnrollmentPath(path), false, path);
  }
});

test('the deploy entry does not load the retired enrollment and lifecycle stack', () => {
  const retired = ['lifecycle.js', 'enrollment-http.js', 'enrollment.js', 'delivery-proof.js',
    'destination-provisioning.js', 'cp-client.js', 'destination-cloudflare.js', 'code-mail.js',
    'enrollment-begin-proof.js', 'enrollment-preflight.js', 'enrollment-grant.js', 'enrollment-provider.js',
    'enrollment-reservations.js', 'enrollment-challenge.js', 'response-signing.js'];
  for (const entry of ['worker.js', 'production.js']) {
    const loaded = closure(entry);
    assert.equal(loaded.has('legacy-paths.js'), true, entry);
    assert.deepEqual(retired.filter((file) => loaded.has(file)), [], entry);
  }
});
