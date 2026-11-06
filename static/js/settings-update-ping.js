// Settings -> System "Update check" section (daily update-check ping,
// mc/update_ping.py). Its own module so settings-drill.js only imports it;
// the disclosure text lives in update-ping-notice.js and is reused, not copied.
//
// The toggle is an ordinary Settings key (update_ping_enabled) saved through
// toggleSetting -> PUT /api/config like every other row. The disclosure flag
// and the endpoint URL are not settable from here.
import { pingStatus } from './update-ping-notice.js';

export function updatePingSettingsHTML(cfg) {
  return `<div class="settings-section">
      <div class="settings-section-title">Update check</div>
      <div class="settings-row">
        <div>
          <div class="settings-label">Count my install and tell me about updates</div>
          <div class="settings-hint">Once a day Clayrune sends one small message to clayrune.io: a random install ID, your Clayrune version, and your operating system. Nothing else. No name, no computer name, no projects, no files. It replies with the newest version so Clayrune can tell you when an update is out. Setting DO_NOT_TRACK=1 or CLAYRUNE_NO_PING=1 in the environment turns it off regardless of this switch.</div>
          <div class="settings-hint" id="update-ping-env-note" style="display:none;color:var(--amber-text,#c98a2c);margin-top:4px"></div>
        </div>
        <div class="settings-toggle ${cfg.update_ping_enabled !== false ? 'on' : ''}" id="update-ping-toggle" onclick="toggleSetting(this,'update_ping_enabled')"></div>
      </div>
    </div>`;
}

// A note when the environment forces the ping off, so a toggle that reads ON is
// not a lie. Called after the Settings DOM exists.
export async function refreshUpdatePingSection() {
  const note = document.getElementById('update-ping-env-note');
  const toggle = document.getElementById('update-ping-toggle');
  if (!note) return;
  const st = await pingStatus();
  if (st && st.env_disabled) {
    note.style.display = '';
    note.textContent = 'Off: DO_NOT_TRACK or CLAYRUNE_NO_PING is set in the environment, which overrides this switch.';
    if (toggle) toggle.classList.remove('on');
  }
}
