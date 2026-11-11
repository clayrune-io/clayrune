// A slow revive and a dead mobile socket look identical until we probe.
function watchSend(statusUrl, onPending) {
  const send = new AbortController();
  const probe = new AbortController();
  let finished = false;
  let probeTimer;
  const hardTimer = setTimeout(() => send.abort(), 120000);
  const softTimer = setTimeout(async () => {
    probeTimer = setTimeout(() => probe.abort(), 5000);
    try {
      const response = await fetch(statusUrl, { signal: probe.signal });
      if (!response.ok) throw new Error(`Status probe: ${response.status}`);
      await response.json();
      if (!finished && !send.signal.aborted) onPending();
    } catch (_) {
      if (!finished) send.abort();
    } finally {
      clearTimeout(probeTimer);
    }
  }, 12000);
  return {
    signal: send.signal,
    finish() {
      finished = true;
      clearTimeout(softTimer);
      clearTimeout(hardTimer);
      clearTimeout(probeTimer);
      probe.abort();
    },
  };
}

// Old occurrences of the same prompt cannot acknowledge this send. Compare
// counts after reconciliation, including the server's user label/clock wrapper.
function hasNewPrompt(before, after, message) {
  const count = lines => (lines || []).filter(line => {
    const clean = window.stripSysPreamble(line).trim();
    if (!clean.startsWith('> ')) return false;
    const prompt = clean.slice(2).replace(/^\[queued\]\s*/, '');
    return prompt === message || prompt.replace(/^[^:\r\n]+:\s*/, '') === message;
  }).length;
  return count(after) > count(before);
}

window.mcSendTimeout = { watchSend, hasNewPrompt };
