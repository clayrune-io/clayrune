// MC-1115: which picker route a SAVED account's Desk permissions follow.
// A saved account reopens in the wizard as type "account" (not a picker type), so the Permissions step
// had no route and offered no Read/Post rows. The reopen puts the route's id in `sel.variant` instead,
// and Permissions looks it up in the picker like any other variant. This only chooses; it grants nothing.
//
// The API route when the account holds its own app sign-in (`credentials.oauth_vault`: the route that can
// post); otherwise the route its browser setup was saved on. A browser sign-in kept beside an API one keeps
// its reading section and its saved scopes ("Kept as they are"). null = no route this account is connected through.
export function savedRouteId(info, ch) {
  const variants = type => ((info?.picker || []).find(t => t.id === type)?.variants || []).filter(v => v.route_id && v.transport !== 'mcp');
  const runnable = v => Object.keys(v.runtime?.purposes || {}).length > 0;
  const routes = [];
  if (ch?.credentials?.oauth_vault) routes.push(...variants('api'));
  const saved = ch?.browser_setup?.route_id;
  if (saved) routes.push(...variants('signin').filter(v => v.route_id === saved));
  return routes.find(runnable)?.id || null;
}
