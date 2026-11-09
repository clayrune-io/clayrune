// HTTP upload bounds are independent of the signed timestamp's validity window.
export const BODY_TIMEOUT_MS = 10_000;
export async function readRequestBody(stream, { timeoutMs = BODY_TIMEOUT_MS, limit = 131_072 } = {}) {
  const reader = stream?.getReader();
  if (!reader) throw new Error('body_limit');
  const chunks = []; let size = 0; let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('body_timeout')), timeoutMs);
  });
  try {
    while (true) {
      const { done, value } = await Promise.race([reader.read(), deadline]);
      if (done) break;
      size += value.byteLength;
      if (size > limit) throw new Error('body_limit');
      chunks.push(value);
    }
    const out = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { out.set(chunk, offset); offset += chunk.byteLength; }
    return out;
  } catch (error) {
    // A producer's cancel promise may never resolve; it cannot extend our deadline.
    void reader.cancel().catch(() => console.error('[agent-mail] upload_cancel_failed'));
    throw error;
  } finally { clearTimeout(timer); reader.releaseLock(); }
}
