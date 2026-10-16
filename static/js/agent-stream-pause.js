// Release this tab's HTTP/1 connection slots for a service-worker update.
// Agents keep running; status reconciliation resumes streams from their cursors.
let pauses = 0;
const projects = new Set();

export function deferAgentStream(projectId) {
  if (!pauses) return false;
  projects.add(projectId);
  return true;
}
window.deferAgentStream = deferAgentStream;

export async function withPausedAgentStreams(action, { resume = () => true } = {}) {
  pauses++;
  try {
    for (const [sid, stream] of Object.entries(agentEventSources)) {
      const pid = (agentStatusCache[sid] || {}).projectId;
      if (pid) projects.add(pid);
      stream.close();
      delete agentEventSources[sid];
      if (agentSSEWatchdogs[sid]) {
        clearInterval(agentSSEWatchdogs[sid]);
        delete agentSSEWatchdogs[sid];
      }
    }
    return await action();
  } finally {
    if (--pauses === 0 && resume()) {
      const pending = [...projects];
      projects.clear();
      for (const pid of pending) {
        window.fetchAgentStatus(pid).catch(e => console.warn('Stream resync failed', e));
      }
    } else if (!pauses) {
      // Navigation is pending. Keep connection slots free until this document
      // unloads; reconnecting now can starve its replacement document's load.
      pauses = 1;
    }
  }
}
