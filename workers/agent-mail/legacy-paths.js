// Retired push-transport route grammar. worker.js answers 410 `legacy_retired`
// for exactly these POST paths and executes nothing behind them, so the entry
// must not load the retired enrollment/lifecycle stack to recognise them.
// A closed list, no imports: test/legacy-paths.test.js pins it to the route
// tables lifecycle.js and enrollment-http.js still export.
export const RETIRED_LEGACY_PATHS = Object.freeze([
  '/v1/mail/send', '/v1/mail/reconcile',
  ...['cancel', 'revoke', 'status', 'renew', 'proof-arm', 'activate'].map((r) => `/v1/mail/lifecycle/${r}`),
  ...['begin', 'confirm', 'complete'].map((r) => `/v1/mail/enrollment/${r}`),
]);
export const isRetiredLegacyPath = (pathname) => RETIRED_LEGACY_PATHS.includes(pathname);
