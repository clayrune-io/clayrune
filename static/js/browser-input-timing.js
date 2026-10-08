// Opt-in observer only. WeakMap metadata never enters /api/browser/input.
const LIMIT = 500, BATCH = 50, FRAME_WAIT_MS = 10000;
const bodies = new WeakMap();
const pending = new Set();
const active = new Set();
const epochs = new WeakMap();
const ready = [];
const clientId = Math.floor(Math.random() * Number.MAX_SAFE_INTEGER);
let sampleId = 0, requestId = 0, eventAt = null, timer = null, uploading = false;
let enabled = false;
let frameEpoch = 0, closedEpoch = -1;
try {
  const flag = new URLSearchParams(location.search).get('bp_input_timing');
  enabled = flag === '1' || (flag !== '0' && localStorage.getItem('mc_bp_input_timing') === '1');
} catch (e) { /* query/storage unavailable: leave diagnostics off */ }

function finish(sample, outcome, at = null) {
  if (!pending.delete(sample)) return;
  active.delete(sample);
  sample.outcome = outcome; sample.t_frame = at;
  ready.push(sample);
  if (ready.length > LIMIT) ready.shift();
}

async function flush() {
  if (uploading || !ready.length) return;
  uploading = true;
  const samples = ready.splice(0, BATCH);
  try {
    const res = await fetch((window.API_BASE || '') + '/api/browser/input-timing', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_agent: navigator.userAgent.slice(0, 512), samples }),
      signal: typeof AbortSignal !== 'undefined' && AbortSignal.timeout ? AbortSignal.timeout(5000) : undefined,
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
  } catch (e) {
    ready.unshift(...samples);
    ready.splice(LIMIT);
    console.warn('Browser input timing upload failed; bounded samples retained for retry.');
  } finally { uploading = false; }
}

function tick() {
  const now = performance.now();
  for (const sample of pending) {
    if (sample.t_ack !== null && now - sample.t_ack >= FRAME_WAIT_MS) finish(sample, 0);
  }
  flush();
}

export const inputTiming = {
  observeInput(element) {
    if (!enabled) return;
    for (const kind of ['keydown', 'input', 'beforeinput', 'compositionupdate', 'compositionend', 'paste']) {
      element.addEventListener(kind, () => {
        const at = performance.now(); eventAt = at;
        queueMicrotask(() => { if (eventAt === at) eventAt = null; });
      }, true);
    }
  },
  queued(body, depth) {
    if (!enabled) return;
    if (!timer) timer = setInterval(tick, 1000);
    if (active.size >= LIMIT) return;
    const sample = {
      client_id: clientId, sample_id: ++sampleId, request_id: 0,
      queue_depth: Math.min(depth, LIMIT), t_key: eventAt ?? performance.now(),
      t_sent: 0, t_ack: null, t_frame: null, outcome: 0, http_status: 0, transport: 0,
    };
    active.add(sample); epochs.set(sample, frameEpoch);
    bodies.set(body, [sample]);
  },
  merged(result, a, b) {
    if (!enabled) return;
    bodies.set(result, [...(bodies.get(a) || []), ...(bodies.get(b) || [])].slice(-LIMIT));
    if (a !== result) bodies.delete(a);
    if (b !== result) bodies.delete(b);
  },
  sent(body) {
    if (!enabled) return null;
    const samples = bodies.get(body) || [];
    bodies.delete(body);
    const at = performance.now(), id = ++requestId;
    for (const sample of samples) {
      sample.t_sent = at; sample.request_id = id;
      sample.transport = window._bpStats?.transport === 'bin' ? 1 : window._bpStats?.transport === 'sse' ? 2 : 0;
      pending.add(sample);
      if (epochs.get(sample) <= closedEpoch) finish(sample, 3);
    }
    return samples;
  },
  ack(samples, res) {
    if (!samples) return;
    const at = performance.now();
    for (const sample of samples) {
      if (!pending.has(sample)) continue;
      sample.t_ack = at; sample.http_status = res.status;
      if (!res.ok) finish(sample, 2);
    }
  },
  failed(samples) { for (const sample of samples || []) finish(sample, 2); },
  observeFrames(img) {
    if (!enabled) return () => {};
    const epoch = ++frameEpoch;
    let closed = false;
    const loaded = () => {
      const loadedAt = performance.now();
      requestAnimationFrame(() => {
        if (closed || document.hidden || !img.isConnected || !img.getClientRects().length) return;
        const at = performance.now();
        for (const sample of pending) {
          if (epochs.get(sample) === epoch && sample.t_ack !== null && loadedAt > sample.t_ack) {
            if (at - sample.t_ack >= FRAME_WAIT_MS) finish(sample, 0);
            else finish(sample, 1, at);
          }
        }
      });
    };
    img.addEventListener('load', loaded);
    return () => {
      closed = true; img.removeEventListener('load', loaded);
      closedEpoch = Math.max(closedEpoch, epoch);
      for (const sample of pending) { if (epochs.get(sample) === epoch) finish(sample, 3); }
      flush();
    };
  },
};
