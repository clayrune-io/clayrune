// ── Terminal pop-out state ─────────────────────────────────────────────────
// Shared BY IDENTITY with the inline script: index.html keeps guarded
// parse-time inits (`window.x = window.x || ...`) at the original state-
// block location, so the props exist before any reader can fire; the
// re-inits below `||`-no-op against them. Inline readers — the
// [terminal:...] marker scan in connectAgentStream's onmessage and the
// beforeunload SSE cleanup — resolve the same window props.
window.terminalInstances = window.terminalInstances || {};      // session_id → Terminal (xterm.js)
window.terminalEventSources = window.terminalEventSources || {};   // session_id → EventSource
window.terminalOutputBuffers = window.terminalOutputBuffers || {};  // session_id → string[]
window.terminalOutputCount = window.terminalOutputCount || {};   // session_id → int
window.terminalDismissed = window.terminalDismissed || new Set(); // session IDs closed by user — don't reopen from replayed markers
window.terminalIsPty = window.terminalIsPty || {};               // session_id → bool (MC-928: real PTY vs pipe+shim)

// ── Terminal Pop-Out ──────────────────────────────────────────────────────

function openTerminalPopout(projectId, sessionId, command, isPty) {
  const modalId = `__terminal_${sessionId}`;

  // If already open, focus it (and leave the recorded mode alone — a second
  // caller that doesn't know is_pty must not flip a live PTY pop-out to pipe)
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    return;
  }
  terminalIsPty[sessionId] = !!isPty;

  const cmdLabel = (command || 'Terminal').substring(0, 60);
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  content.style.cssText = 'padding:0;display:flex;flex-direction:column';
  content.innerHTML = `
    <div class="terminal-header modal-header">
      <span class="terminal-status-dot running" id="term-dot-${esc(sessionId)}"></span>
      <span class="terminal-cmd">$ ${esc(cmdLabel)}</span>
      <button class="btn-terminal-stop" id="term-stop-${esc(sessionId)}" onclick="stopTerminal('${esc(sessionId)}')">Stop</button>
      <div style="display:flex;align-items:center;gap:2px;margin-left:auto;margin-right:8px">
        <button onclick="termZoom('${esc(sessionId)}',-1)" style="background:none;border:1px solid var(--border);color:var(--text-dim);width:24px;height:24px;border-radius:4px;cursor:pointer;font-size:14px;line-height:1;padding:0" title="Decrease font size">&#x2212;</button>
        <span id="term-fontsize-${esc(sessionId)}" style="font-size:11px;color:var(--text-faint);min-width:22px;text-align:center"></span>
        <button onclick="termZoom('${esc(sessionId)}',1)" style="background:none;border:1px solid var(--border);color:var(--text-dim);width:24px;height:24px;border-radius:4px;cursor:pointer;font-size:14px;line-height:1;padding:0" title="Increase font size">+</button>
      </div>
      <div class="modal-window-controls" style="position:static;margin:0">
        <button class="modal-minimize" onclick="minimizeModal('${esc(modalId)}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${esc(modalId)}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="terminal-container" id="terminal-container-${esc(sessionId)}"></div>
    <div class="terminal-stdin-row" id="terminal-stdin-row-${esc(sessionId)}" ${isPty ? 'style="display:none"' : ''}>
      <input type="text" class="terminal-stdin-input" id="terminal-stdin-${esc(sessionId)}"
        placeholder="Send input to process..."
        onkeydown="if(event.key==='Enter'){sendTerminalInput('${esc(sessionId)}');event.preventDefault()}"
      >
      <button class="btn-dispatch" style="padding:6px 14px" onclick="sendTerminalInput('${esc(sessionId)}')">Send</button>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  // Size and position. The size goes on the .modal-content (like every other
  // modal), NOT the .modal-window wrapper: the resize handles hang off the
  // wrapper and drag the content, so a wrapper wider than the content left the
  // east handle in dead space 200px right of the visible edge, and the
  // sheet could not be widened by grabbing the border you can see. On mobile
  // (≤960px) the stylesheet's full-width sheet rule applies, so no inline size.
  if (!(_isMobileDevice || window.innerWidth <= 960)) _clampModalSize(content, 900, 600);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z, terminalSessionId: sessionId });
  centerModalElement(win);
  focusModal(modalId);

  // Initialize xterm.js after DOM is ready
  setTimeout(async () => {
    await ensureXterm();
    initTerminalXterm(sessionId, !!isPty);
    connectTerminalStream(projectId, sessionId);
  }, 50);
}

// xterm's JS + CSS + fit addon used to be render-blocking <script>/<link> tags
// in the document head — ~1 MB from jsdelivr on every boot for a pop-out most
// sessions never open. Loaded here instead, once, on the first terminal.
let _xtermPromise = null;
function ensureXterm() {
  if (window.Terminal && window.FitAddon) return Promise.resolve();
  if (_xtermPromise) return _xtermPromise;
  const css = document.createElement('link');
  css.rel = 'stylesheet';
  css.href = 'https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css';
  document.head.appendChild(css);
  const load = src => new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src; s.onload = resolve; s.onerror = () => reject(new Error('failed to load ' + src));
    document.head.appendChild(s);
  });
  // Sequential: the fit addon registers against the Terminal global.
  _xtermPromise = load('https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js')
    .then(() => load('https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js'))
    .catch(e => { _xtermPromise = null; console.warn('[Clayrune] xterm failed to load:', e); });
  return _xtermPromise;
}

function initTerminalXterm(sessionId, isPty) {
  const container = document.getElementById(`terminal-container-${sessionId}`);
  if (!container || terminalInstances[sessionId]) return;
  if (!window.Terminal || !window.FitAddon) {
    container.innerHTML = '<div style="padding:16px;color:#f87171;font-size:12px">'
      + 'Terminal renderer unavailable — xterm.js could not be loaded (offline?).</div>';
    return;
  }

  const term = new Terminal({
    theme: {
      background: '#0a0c10',
      foreground: '#e2e8f0',
      cursor: '#4da6ff',
      selectionBackground: '#3d4460',
      black: '#0f1117',
      red: '#f87171',
      green: '#34d399',
      yellow: '#fbbf24',
      blue: '#4da6ff',
      magenta: '#a78bfa',
      cyan: '#22d3ee',
      white: '#f1f5f9',
    },
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: _isMobileDevice ? 9 : 12,
    lineHeight: 1.4,
    cursorBlink: !!isPty,
    // Pipe sessions: input goes through the "Send input" box below (line-
    // buffered, matches the pipe's own stdin). Real-PTY sessions (MC-928)
    // need raw keystrokes — arrow keys, single chars with no Enter — for an
    // Ink TUI like gemini's account picker to be navigable at all, so stdin
    // is enabled and wired to onData() below instead.
    disableStdin: !isPty,
    convertEol: false,
    scrollback: 5000,
  });

  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(container);

  // Fit to the container and, for a real PTY, tell the server so the child's
  // ioctl window size (and anything that queries it, e.g. an Ink TUI's layout)
  // matches what is on screen. The PTY is spawned at 120x30 and the first fit
  // is narrower, so the first call MUST post — without it a TUI lays out for
  // 120 columns and wraps in a narrower xterm. Deduped on cols x rows.
  let lastSent = '';
  const fitAndSync = () => {
    try {
      fitAddon.fit();
      if (isPty && term.cols && term.rows) {
        const dims = term.cols + 'x' + term.rows;
        if (dims === lastSent) return;
        lastSent = dims;
        fetch(API_BASE + '/api/terminal/resize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sessionId, cols: term.cols, rows: term.rows }),
        }).catch(() => {});
      }
    } catch {}
  };
  fitAndSync();

  terminalInstances[sessionId] = term;
  terminalInstances[sessionId]._fitAddon = fitAddon;
  terminalInstances[sessionId]._fitAndSync = fitAndSync;
  const sizeLabel = document.getElementById('term-fontsize-' + sessionId);
  if (sizeLabel) sizeLabel.textContent = term.options.fontSize;

  if (isPty) {
    term.onData((data) => {
      fetch(API_BASE + '/api/terminal/stdin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, text: data }),
      }).catch(() => {});
    });
  }

  // Re-fit whenever the container is resized (window widened, mobile rotate).
  new ResizeObserver(fitAndSync).observe(container);

  // FitAddon subtracts the viewport's scrollbar width, but xterm only measures
  // that width after its first render — the fit above ran with it still 0, so
  // the grid came out ~15px too wide and the right-hand columns sat under the
  // classic scrollbar (box-drawing, long lines and TUI borders clipped). The
  // container size does not change when the measurement lands, so the
  // ResizeObserver never fires; re-fit when the viewport's client width does.
  let lastClientW = -1;
  term.onRender(() => {
    const vp = term.element && term.element.querySelector('.xterm-viewport');
    if (vp && vp.clientWidth !== lastClientW) {
      lastClientW = vp.clientWidth;
      fitAndSync();
    }
  });

  // Write any buffered output
  const buf = terminalOutputBuffers[sessionId] || [];
  for (const line of buf) {
    term.write(line);
  }
}

function termZoom(sessionId, delta) {
  const term = terminalInstances[sessionId];
  if (!term) return;
  const newSize = Math.max(5, Math.min(24, term.options.fontSize + delta));
  term.options.fontSize = newSize;
  const label = document.getElementById('term-fontsize-' + sessionId);
  if (label) label.textContent = newSize;
  // A new font size changes the column count — a PTY must hear about it too.
  if (term._fitAndSync) term._fitAndSync(); else { try { term._fitAddon.fit(); } catch {} }
}


function connectTerminalStream(projectId, sessionId) {
  if (terminalEventSources[sessionId]) {
    terminalEventSources[sessionId].close();
  }

  const since = terminalOutputCount[sessionId] || 0;
  const es = new EventSource(API_BASE + `/api/terminal/stream?session=${sessionId}&since=${since}`);
  terminalEventSources[sessionId] = es;

  es.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'output') {
        if (!terminalOutputBuffers[sessionId]) terminalOutputBuffers[sessionId] = [];
        terminalOutputBuffers[sessionId].push(msg.text);
        terminalOutputCount[sessionId] = (terminalOutputCount[sessionId] || 0) + 1;
        // Cap buffer
        if (terminalOutputBuffers[sessionId].length > 5000) {
          terminalOutputBuffers[sessionId] = terminalOutputBuffers[sessionId].slice(-3000);
        }
        const term = terminalInstances[sessionId];
        if (term) term.write(msg.text);
      } else if (msg.type === 'status') {
        const term = terminalInstances[sessionId];
        if (term) {
          term.writeln(`\r\n\x1b[90m[Process ${msg.status} — exit code ${msg.exit_code}]\x1b[0m`);
        }
        // Update status dot and hide stop button
        const dot = document.getElementById(`term-dot-${sessionId}`);
        if (dot) { dot.className = `terminal-status-dot ${msg.status}`; }
        const stopBtn = document.getElementById(`term-stop-${sessionId}`);
        if (stopBtn) stopBtn.style.display = 'none';
        es.close();
        delete terminalEventSources[sessionId];
      } else if (msg.type === 'error') {
        es.close();
        delete terminalEventSources[sessionId];
        // MC-959: a pop-out that already shows output (e.g. an install that
        // FAILED) must stay open so the error can be read — say the session
        // ended instead of closing it. Only a never-attached one is closed.
        if ((terminalOutputCount[sessionId] || 0) > 0) {
          const term = terminalInstances[sessionId];
          if (term) term.writeln('\r\n\x1b[90m[Session no longer on the server — output above is final]\x1b[0m');
          const stopBtn = document.getElementById(`term-stop-${sessionId}`);
          if (stopBtn) stopBtn.style.display = 'none';
          return;
        }
        // Session doesn't exist on server — close the pop-out
        terminalDismissed.add(sessionId);
        const modalId = `__terminal_${sessionId}`;
        if (openModals.has(modalId)) closeModalById(modalId);
      }
    } catch {}
  };

  es.onerror = () => {
    es.close();
    delete terminalEventSources[sessionId];
  };
}

async function sendTerminalInput(sessionId) {
  const input = document.getElementById(`terminal-stdin-${sessionId}`);
  if (!input) return;
  const text = input.value;
  input.value = '';

  // A real PTY echoes what it receives and submits on Enter (CR); a bare LF
  // does not submit in a TUI. A pipe has no echo and wants a newline.
  const pty = !!terminalIsPty[sessionId];
  if (!pty) {
    // Echo the input in cyan
    const term = terminalInstances[sessionId];
    if (term) term.writeln(`\x1b[36m${text}\x1b[0m`);
  }

  await fetch(API_BASE + `/api/terminal/stdin`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ session_id: sessionId, text: text + (pty ? '\r' : '\n') })
  }).catch(() => {});
}

async function stopTerminal(sessionId) {
  await fetch(API_BASE + `/api/terminal/stop`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ session_id: sessionId })
  }).catch(() => {});

  if (terminalEventSources[sessionId]) {
    terminalEventSources[sessionId].close();
    delete terminalEventSources[sessionId];
  }
  const dot = document.getElementById(`term-dot-${sessionId}`);
  if (dot) dot.className = 'terminal-status-dot stopped';
  const stopBtn = document.getElementById(`term-stop-${sessionId}`);
  if (stopBtn) stopBtn.style.display = 'none';
}

async function fetchTerminalStatus(projectId) {
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/terminal/status`);
    const data = await res.json();
    const sessions = data.sessions || [];
    for (const s of sessions) {
      const modalId = `__terminal_${s.session_id}`;
      if (s.status === 'running' && !openModals.has(modalId)) {
        // Restore buffer and open pop-out
        terminalOutputBuffers[s.session_id] = s.output_lines || [];
        terminalOutputCount[s.session_id] = (s.output_lines || []).length;
        openTerminalPopout(projectId, s.session_id, s.command, s.is_pty);
      }
    }
  } catch {}
}

// Clean up xterm instance when terminal modal is closed
function cleanupTerminalModal(modalId) {
  const match = modalId.match(/^__terminal_(.+)$/);
  if (!match) return;
  const sessionId = match[1];
  // Mark as dismissed so replayed markers don't reopen it
  terminalDismissed.add(sessionId);
  // Close SSE
  if (terminalEventSources[sessionId]) {
    terminalEventSources[sessionId].close();
    delete terminalEventSources[sessionId];
  }
  if (terminalInstances[sessionId]) {
    terminalInstances[sessionId].dispose();
    delete terminalInstances[sessionId];
  }
  delete terminalOutputBuffers[sessionId];
  delete terminalOutputCount[sessionId];
  delete terminalIsPty[sessionId];
  // Tell server to remove the session entirely (kills process if still running)
  fetch(API_BASE + `/api/terminal/delete`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ session_id: sessionId })
  }).catch(() => {});
}


// ── ES-module interop ───────────────────────────────────────────────────────
// Re-expose page-called functions on window. Inbound inline callers
// (openProjectModal → fetchTerminalStatus, closeModalById →
// cleanupTerminalModal, agent-output marker scan → openTerminalPopout)
// and this module's own generated on*= attributes (stopTerminal,
// termZoom, sendTerminalInput) resolve against the global object at
// call time. initTerminalXterm/connectTerminalStream are module-private
// (only called from openTerminalPopout). State is bridged by identity
// via the window props above; no accessor bridges needed (formal
// generated-handler assignment scan empty; zero wholesale reassignments).
window.openTerminalPopout = openTerminalPopout;
window.fetchTerminalStatus = fetchTerminalStatus;
window.cleanupTerminalModal = cleanupTerminalModal;
window.stopTerminal = stopTerminal;
window.termZoom = termZoom;
window.sendTerminalInput = sendTerminalInput;
