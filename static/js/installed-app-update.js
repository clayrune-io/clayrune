import { withPausedAgentStreams } from './agent-stream-pause.js';

export async function forceUpdateServiceWorker() {
  if (!navigator.serviceWorker) return;
  const btn = document.getElementById('installed-app-update-btn');
  const hint = document.getElementById('installed-app-update-hint');
  if (btn?.disabled) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
  if (hint) hint.textContent = 'Checking for a new version…';
  let reloaded = false;
  let reloadPending = false;
  const onController = () => {
    if (!reloaded) { reloaded = true; location.reload(); }
  };
  try {
    await withPausedAgentStreams(async () => {
      let timer;
      let expired = false;
      const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => {
          expired = true;
          reject(new Error('The update check timed out. Try again after closing other Clayrune tabs.'));
        }, 12000);
      });
      let reg;
      try {
        reg = await Promise.race([(async () => {
          const registration = await navigator.serviceWorker.getRegistration('/');
          if (registration && !expired) {
            navigator.serviceWorker.addEventListener('controllerchange', onController);
            await registration.update();
          }
          return registration;
        })(), timeout]);
      } finally { clearTimeout(timer); }
      if (!reg) { showToast('No service worker registered.', 4000); return; }
      let waiting = reg.waiting;
      if (!waiting && reg.installing) {
        waiting = await new Promise(resolve => {
          const worker = reg.installing;
          const finish = value => {
            clearTimeout(installTimer);
            worker.removeEventListener('statechange', onChange);
            resolve(value);
          };
          const onChange = () => {
            if (worker.state === 'installed') finish(reg.waiting || worker);
            else if (worker.state === 'redundant') finish(null);
          };
          const installTimer = setTimeout(() => finish(reg.waiting || null), 8000);
          worker.addEventListener('statechange', onChange);
          onChange();
        });
      }
      if (!waiting) { if (!reloaded) showToast('Already up to date.'); return; }
      waiting.postMessage({ type: 'SKIP_WAITING' });
      reloadPending = true;
      setTimeout(onController, 6000);
    }, { resume: () => !reloadPending && !reloaded });
  } catch (e) {
    showToast(`Update check failed: ${e.message || e}`, 5000);
  } finally {
    if (!reloadPending) navigator.serviceWorker.removeEventListener('controllerchange', onController);
    if (btn?.isConnected) { btn.disabled = false; btn.textContent = 'Force update service worker'; }
    if (hint?.isConnected) hint.textContent = 'Checks for a new version, activates it, and reloads.';
  }
}
