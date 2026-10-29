// Execute the actual browser-read JS in Chromium, without network or a saved profile.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const cwd = fileURLToPath(new URL('../../', import.meta.url));
const template = execFileSync(process.env.MC_PYTHON || 'python', ['-c',
  'from mc.blueprints.browser_routes import _READ_JS_TEMPLATE; print(_READ_JS_TEMPLATE)'],
{ cwd, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
const browser = await chromium.launch({ headless: true });
let checks = 0;
try {
  const page = await browser.newPage();
  const read = async (selector = null, cap = 450000) => page.evaluate(
    template.replace('__SEL__', JSON.stringify(selector)).replace('__CAP__', String(cap)));
  const visible = (r) => r.runs.filter((x) => !x.hidden).map((x) => x.text).join('\n');
  const check = (condition, message) => { assert.ok(condition, message); checks++; };

  await page.setContent(`<body><code style="display:none">${'voyager-json'.repeat(50000)}</code>
    <script type="application/json">{"data":"not rendered"}</script>
    <div hidden><span style="display:block">hidden ancestor payload</span></div>
    <div style="display:none"><code>nested payload</code></div>
    <p>0 notifications total</p><code>visible example()</code>
    <p style="visibility:hidden">hidden visibility</p><p style="opacity:0">hidden opacity</p>
    <p style="position:absolute;left:-9999px">offscreen text</p>
    <p style="font-size:1px">tiny text</p><p style="color:white;background:white">invisible ink</p>
    <!-- hidden comment --><img alt="attribute payload"></body>`);
  const result = await read();
  check(!result.error, 'reader executes successfully');
  check(visible(result) === '0 notifications total\nvisible example()', 'hidden payloads excluded; visible code retained');
  check(result.data_island_count === 4, 'counts maximal hidden data subtrees, including JSON script');
  check(result.runs.every((x) => !/voyager|payload|not rendered/.test(x.text)), 'data never enters CDP text runs');
  check(!result.js_capped, 'hidden megabyte does not consume the visible character cap');
  for (const reason of ['visibility_hidden', 'zero_opacity', 'offscreen', 'tiny_font', 'low_contrast']) {
    check(result.runs.some((x) => x.hidden === reason), `retains ${reason} detection`);
  }
  check(result.comment_count === 1 && result.attr_text_count === 1, 'comment and attribute flags retained');
  const hiddenSelection = await read('div[hidden] span');
  check(hiddenSelection.runs.length === 0 && hiddenSelection.data_island_count === 1, 'selector cannot escape a hidden ancestor');
  const codeSelection = await read('code[style]');
  check(codeSelection.runs.length === 0 && codeSelection.data_island_count === 1, 'hidden selector root is counted and excluded');
  const visibleSelection = await read('p');
  check(visible(visibleSelection) === '0 notifications total', 'visible selector root text is collected');
  const missing = await read('#missing');
  check(missing.error === 'selector_not_found', 'selector refusal preserved');
  const capped = await read(null, 5);
  check(capped.js_capped && visible(capped) === '0 notifications total', 'visible text still triggers the character cap');
  await page.setContent('<div hidden>payload</div>'.repeat(40001) + '<p>beyond traversal cap</p>');
  const bounded = await read();
  check(bounded.data_island_count === 39999 && !visible(bounded), 'rejected nodes retain the traversal bound');
  console.log(`PASS browser-read-data-islands (${checks} checks)`);
} finally {
  await browser.close();
}
