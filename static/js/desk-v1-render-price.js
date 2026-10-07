// A prepared picture price survives the next free text-only quote, until an edit/reprice.
export function confirmationMatches(confirmation, request, fresh) {
  return !!confirmation && confirmation.request === JSON.stringify(request)
    && confirmation.base === JSON.stringify(fresh);
}

export function priceConfirmation(error, request, fresh) {
  const b = error && error.body;
  if (!b || error.status !== 409 || b.code !== 'render_price_changed' || !b.estimate || !b.plan) return null;
  return { request: JSON.stringify(request), base: JSON.stringify(fresh),
    quote: { ...fresh, estimate: b.estimate, plan: b.plan, refusal: null } };
}
