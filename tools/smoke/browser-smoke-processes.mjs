// Register only PIDs owned by this smoke; teardown stays with its caller.
export async function registerBrowserSmokeProcess(pid, name, command) {
  const url = process.env.MC_TEST_PROCESS_REGISTER_URL;
  if (!url) return; // Standalone CI stays hermetic; managed runs set this URL.
  const response = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pid, name, project_id: 'mission_control', command }),
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`Smoke process registration failed: HTTP ${response.status}`);
}
