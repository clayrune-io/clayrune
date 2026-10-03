#!/usr/bin/env node
// Backlog 7041d789: the auth banner's Claude sign-in button ignored
// claude_signin_channel=terminal — it POSTed /api/claude/login-launch (a
// host-OS terminal window) instead of the real-PTY path Settings uses
// (auth-login-remote -> _claude_terminal_signin, MC-928). Runs the real
// provider-auth.js in a tiny DOM shell and clicks the banner button.
//   node auth-banner-claude-signin-terminal.mjs
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(here, '..', '..', 'static', 'js', 'provider-auth.js'), 'utf8');
const fail = (m) => { console.error('FAIL ' + m); process.exit(1); };

async function run(label, authLoginRemoteReply) {
  const calls = [], popouts = [], alerts = [];
  const signinBtn = { textContent: '', onclick: null, disabled: false };
  const elements = {
    'auth-banner': { classList: { add() {}, remove() {} } },
    'auth-banner-text': { textContent: '' },
    'auth-banner-signin': signinBtn,
  };
  const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });
  const context = vm.createContext({
    window: { currentProjectId: 'p1' }, console, API_BASE: '',
    _globalConfig: { default_provider: 'claude' },
    _agentProviders: [{ name: 'claude', display_name: 'Claude Code', installed: true, in_use: true, default: true }],
    document: { getElementById: id => elements[id] || null },
    fetch: async (url, opts) => {
      calls.push(`${(opts && opts.method) || 'GET'} ${url}`);
      if (url === '/api/agent/claude/auth-login-remote') return authLoginRemoteReply;
      return reply(200, { ok: true });
    },
    openTerminalPopout: (...a) => popouts.push(a),
    alert: m => alerts.push(m), showToast: () => {}, setTimeout, clearTimeout,
  });
  vm.runInContext(source, context, { filename: 'provider-auth.js' });
  context._renderAuthBanner({ ok: false, reason: 'not_logged_in' });
  if (typeof signinBtn.onclick !== 'function') fail(`${label}: banner button has no handler`);
  await signinBtn.onclick();
  return { calls, popouts, alerts };
}

// channel=terminal with a PTY: server hands back a pty session.
{
  const r = await run('terminal+pty', { ok: true, status: 200, json: async () =>
    ({ ok: true, remote_capable: true, pty: true, session_id: 's9', command: 'claude auth login' }) });
  if (r.calls.some(c => c.includes('/api/claude/login-launch') || c.includes('/login-launch')))
    fail(`banner opened a host-OS terminal: ${JSON.stringify(r.calls)}`);
  if (!r.calls.includes('POST /api/agent/claude/auth-login-remote'))
    fail(`banner did not take the Settings sign-in path: ${JSON.stringify(r.calls)}`);
  if (r.popouts.length !== 1 || r.popouts[0][1] !== 's9' || r.popouts[0][3] !== true)
    fail(`pty pop-out not opened as an interactive pty session: ${JSON.stringify(r.popouts)}`);
}

// channel=terminal with no PTY backend: server 503s; banner must show the
// error, not fall through to a host-OS terminal.
{
  const r = await run('terminal, no pty', { ok: false, status: 503, json: async () =>
    ({ ok: false, remote_capable: false, signin_channel: 'terminal', error: 'needs a real-PTY terminal' }) });
  if (r.calls.some(c => c.includes('login-launch')))
    fail(`no-PTY refusal fell through to a host terminal: ${JSON.stringify(r.calls)}`);
  if (r.alerts.length !== 1 || !r.alerts[0].includes('real-PTY'))
    fail(`refusal not shown to the user: ${JSON.stringify(r.alerts)}`);
  if (r.popouts.length) fail('pop-out opened despite refusal');
}

console.log('PASS auth banner: Claude sign-in takes the real-PTY path under claude_signin_channel=terminal; no host-OS terminal fallback.');
