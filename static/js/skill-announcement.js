// Presentation only: keep original messages in the session log/buffer.
// Deliberately match a single, routine announcement, never a mixed update.
export function hideSkillAnnouncement(provider, text) {
  if (provider !== 'codex' || typeof text !== 'string') return false;
  const line = text.trim();
  if (/\b(approval|permission|confirm|blocked|failed|failure|error|cannot|unable|requires?|must|warning|stop|but|however|found|shows?|reports?|returned|needs?|pending|denied|rejected|broken)\b/i.test(line)) return false;
  return /^I(?:['\u2019]m| am) using (?:the )?[\w -]+ skills? to (?:check|read|review|inspect|find|search|recover|look up|verify|create|edit|build|update|generate|merge) [^.!?\n,:;]+\.?$/i.test(line);
}
