// MC-1062/14: per-service guide entrypoints are retired.
// Provider/sign-in authorization lives in the common Setup adapter; the
// production flow smoke also exercises saved-tile checks and shared copy.
await import('./desk-v1-connect-api-step.mjs');
