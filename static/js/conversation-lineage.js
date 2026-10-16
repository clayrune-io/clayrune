// History reconstruction carries transcript text, while the conversation list
// carries the server's durable spawner-of-record. Never infer a parent from
// task text, a character, or a callback target.
export function historySpawner(sessionId, status, rows) {
  if (status.spawnedBySessionId) return status.spawnedBySessionId;
  const matches = (Array.isArray(rows) ? rows : []).filter(row => {
    if (status.claudeSessionId) return row.claude_session_id === status.claudeSessionId;
    if (status.providerSessionId) return row.provider_session_id === status.providerSessionId
      && row.provider === status.provider;
    return row.mc_session_id === sessionId;
  });
  const parents = new Set(matches.map(row => row.spawned_by_session_id).filter(Boolean));
  if (parents.size !== 1) return '';
  const parent = [...parents][0];
  return parent === sessionId ? '' : parent;
}
