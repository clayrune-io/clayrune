// Shared hermetic harness for the chat-send smokes (send-refused-not-shown-as-sent,
// send-restore-survives-rebuild). Serves the real SPA from memory with Playwright
// routes, opens a parked chat, types `message` and calls the real sendFollowup.
// Never talks to a live agent. Same shape as send-timeout.mjs, trimmed to what
// these two smokes need; send-timeout.mjs keeps its own copy (it drives clocks).
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const html = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);

export const pid = 'send_harness';
export const sid = 'send-harness-session';
export const message = 'Retry me\nSecond line.';
const history = ['> Ron: Earlier prompt', 'Earlier reply'];
const project = {
  id: pid, name: 'Send harness', status: 'active', domain: 'general', emoji: '',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, activity_log: [], backlog: [], project_path: '/smoke/send-harness',
  last_updated: '2026-11-11T00:00:00Z', display_order: 0, provider: 'claude', use_streaming_agent: true,
};

export async function launch() { return chromium.launch(); }

// options: { reply, status, networkError, question }
//   reply         JSON body (+ optional `status`) the /agent/send route answers with
//   networkError  abort the POST instead of answering
//   question      { id, questions } -> the server mirrors it as a pending question
export async function scenario(browser, options, check) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  try {
    const page = await ctx.newPage();
    page.setDefaultTimeout(20000);
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {
      window.smokeStreams = [];
      window.EventSource = class {
        constructor(url) { this.url = url; window.smokeStreams.push(this); }
        close() { this.closed = true; }
      };
    });
    const seen = { posts: 0, sent: false };
    await page.route('**/*', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
      if (path === '/api/projects') return route.fulfill({ json: [project] });
      if (path === '/api/config') return route.fulfill({ json: {} });
      if (path === '/api/characters') return route.fulfill({ json: [] });
      if (route.request().url().startsWith('https://cdn.jsdelivr.net/npm/mermaid@')) {
        return route.fulfill({ contentType: 'text/javascript', body: 'export default {initialize(){},async run(){}};' });
      }
      if (path.endsWith('/agent/status')) {
        const q = options.question;
        return route.fulfill({ json: { sessions: [{
          session_id: sid, status: q ? 'idle' : 'error', log_lines: history,
          ...(q ? { waiting_for_question: true,
            pending_questions: [{ question_id: q.id, questions: q.questions }] } : {}),
        }] } });
      }
      if (path.endsWith('/agent/send')) {
        seen.posts++;
        seen.sent = true;
        if (options.networkError) return route.abort('failed');
        return route.fulfill({ status: options.reply?.status || 200, json: options.reply });
      }
      return route.abort();
    });
    await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(({ pid, sid, history, question }) => {
      const idle = question ? 'idle' : 'error';
      agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: 'Send harness',
        task: 'Parked', status: idle, startedAt: '2026-11-10T00:00:00Z' });
      agentStatusCache[sid] = { status: idle, task: 'Parked', projectId: pid,
        claudeSessionId: 'native-send-harness', startedAt: '2026-11-10T00:00:00Z' };
      agentOutputBuffers[sid] = history.slice();
      agentServerLines[sid] = history.length;
      conversationsCache[pid] = [{ claude_session_id: 'native-send-harness', mc_session_id: sid,
        character: null, status: idle, turns: 1, label: 'Parked', mtime: 1000 }];
      openProjectModal(pid);
      openConversation(pid, 'native-send-harness', sid, true);
      window.smokeToasts = [];
      window.showToast = text => window.smokeToasts.push(text);
    }, { pid, sid, history, question: options.question || null });
    await page.waitForSelector(`#agent-followup-${sid}`);
    await check({
      page,
      input: page.locator(`#agent-followup-${sid}`),
      output: page.locator(`#agent-output-${sid}`),
      seen,
      errors: () => errors,
    });
  } finally {
    await ctx.close();
  }
}

// Type the message and press the real Send path.
export async function send(page) {
  await page.evaluate(({ pid, sid, message }) => {
    document.getElementById(`agent-followup-${sid}`).value = message;
    window.sendFollowup(pid, sid);
  }, { pid, sid, message });
}

export function runner(title) {
  let passed = 0, failed = 0;
  return {
    async step(name, fn) {
      try { await fn(); passed++; console.log(`PASS ${name}`); }
      catch (e) { failed++; console.log(`FAIL ${name}\n  ${String(e.message || e).split('\n').join('\n  ')}`); }
    },
    done() {
      console.log(`\n${title}: ${passed} passed, ${failed} failed`);
      if (failed) process.exitCode = 1;
    },
  };
}
