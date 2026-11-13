// Retained enrollment exception boundary shared with direct compatibility tests.
const reply = (status, value) => Response.json(value, { status, headers: { 'cache-control': 'no-store' } });
export async function enrollmentErrorBoundary(operation) {
  try {
    return await operation();
  } catch {
    console.error('[agent-mail] enrollment_storage_or_configuration_failure');
    return reply(503, { code: 'storage_or_configuration_failure' });
  }
}
