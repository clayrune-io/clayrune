# Automatic owner destination signup (MC-1166 option A)

Disabled branch candidate. No deployment, live provider test, credential creation,
mail enablement or permission change is part of this implementation. This addendum
supersedes the metadata-only begin and seam-only provider descriptions in
`AGENT_MAIL_UNIT7D2E_CONTRACT.md`. The existing confirm/grant/complete ceremony,
mailbox proof, passcode gates and owner-only send policy remain required.

## Signed begin

`POST /v1/mail/enrollment/begin` accepts exactly `{value, flow_secret, proof}`.
The existing 11 begin fields and optional `fence_digest` are unchanged. The
device Ed25519 key signs compact ASCII JSON of this ordered string array:

```text
["clayrune-mail-enrollment-begin", "1", service_origin, timestamp_ms, nonce,
 SHA256(ASCII(flow_secret_hex)),
 flow_id, owner_address, device_id, device_public_key, mail_key_id,
 mail_public_key, tunnel_uuid, route_origin, manifest_sequence, service_key_id,
 service_public_key_sha256, fence_digest_or_empty_string]
```

`proof` is exactly `{timestamp, nonce, signature}`: timestamp is 13-digit decimal
milliseconds, nonce is 32 lowercase hex characters and signature is unpadded
base64url Ed25519. This is a separate purpose from the enrollment transcript R,
mail request signing and the `x-mail-nonce` response-binding nonce. Both runtimes
use the same fixed field order; Python/Worker interoperability is tested.

The existing five-minute signature skew applies. Grammar, signature and current
service pin are checked before CP preflight. CP must still confirm the active
device/key/tunnel/route binding. Then D1 claims `(device_id, proof.nonce)` through
the end of the signature window, before any provisioning. A retry must sign a
new nonce. Failed possession/preflight cannot create a destination or code;
refusal accounting uses the existing shared unauthenticated bucket.

## Provisioning and recovery

Production resolution through `enrollment-provider.js` requires `MAIL_DB`,
`MAIL_DESTINATION_ACCOUNT_ID` (32 lowercase hex account id) and the Worker secret
`MAIL_DESTINATION_TOKEN`. It builds the bounded Cloudflare client and the durable
provisioner. Injected providers/readers remain test seams. No runtime credential
creation exists.

Before a new core flow/reservation/code is created, the provisioner:

1. Claims an account-wide D1 lock before listing Cloudflare destinations. A busy
   claim returns `destination_unavailable`; there is no in-process waiting loop.
2. Maintains one attempt per stable CP device id, independent of flow ids and
   expiring identity reservations. A different address is refused for 24 hours
   from attempt creation (`destination_pending`). Retrying the same address
   never extends its seven-day pending lifetime. Existing verified addresses
   can recover even after that lifetime; expired pending ones report
   `destination_expired`. The service never automatically deletes destinations.
3. Lists the whole account, pending and verified, without an address filter.
   Existing addresses are only reported, never created again. A verified exact
   address permits the normal code ceremony. Pending reports
   `destination_unverified`, with no core flow or code generated.
4. Before a missing-address create, refuses at 190 account addresses, including
   unrelated destinations (`destination_capacity`). Unobserved create claims
   reserve additional capacity, covering slow list visibility and lost replies.
   Once an address is observed in the list, its claim no longer adds another slot.
5. Claims the address durably before POST, subject to rolling 24-hour limits of
   three create claims per device and twenty globally (`destination_rate_limited`).
   Failed/ambiguous calls consume budget conservatively. Create claims are
   address-unique tombstones: a crash immediately before/after the provider call
   can never cause a second POST for that address. A lost response is recovered
   by later listing. An address that never appears remains
   `destination_unavailable`; recovery requires operator investigation, not a
   blind resend. There is no automated tombstone reset or deletion path.

The lock lease is 30 seconds. A suspended invocation must still hold its token
and unexpired lease when it inserts the create claim. Stale locks can be
reclaimed; a prior holder cannot release a successor's token. The address claim
and unobserved-capacity accounting survive lease expiry and Worker restarts.
Lock expiry, address replacement admission, create time, budgets and capacity
are also checked inside SQL using the database clock at the write boundary;
a delayed invocation cannot authorize a create using an old JavaScript time.
This serializes this Worker's account admission; unrelated account administration
can still race it, so the ten-address provider headroom remains necessary.

Provider errors disclose only a constant code. Cloudflare calls use a fixed
account endpoint, no redirects, five-second request and stream deadlines,
128-KiB response bounds and at most ten pages of fifty rows. Pagination must be
complete and consistent before any create. `verified` is a timestamp or null;
a boolean or arbitrary truthy value is refused. A complete empty list may report
zero or one total pages.

## Retry hints and Python state

Provisioning outcomes use **signed HTTP 409** bodies, exactly
`{flow_id, code}`, where code is one of `destination_unverified`,
`destination_unavailable`, `destination_capacity`, `destination_rate_limited`,
`destination_pending`, `destination_expired`. All other existing refusals keep
their original status/signing behavior. These six bodies use the same
service response signature, path, request nonce and flow/generation scope as
ceremony responses. They are hints only, never ownership acknowledgments.

The HTTPS transport retains bounded error headers/bodies. The Python ceremony
client verifies a begin 409 before examining its exact shape and closed code;
unsigned/mismatched hints fall back to ordinary retry failure. Local enrollment
keeps the same sealed pending flow, records no assigned identity and passes
authenticated destination hints to the caller. After the user follows the
Cloudflare verification link, **Resume** retries signed begin; only then is the
Clayrune code generated and sent. Review/Confirm, code verification, dual-key
proof, CP grant and complete still follow. Existing core flows bypass provisioning
on re-begin and preserve code idempotency; complete independently rechecks the
exact verified destination. Neither destination verification nor a retry hint
activates mail.

## Schema and operator handoff

For a new dedicated database: `schema.sql`, then migrations 0001, 0002, 0003,
0004 in order. Existing databases apply only missing migrations; never replay
the bootstrap schema over populated data. Migration `0004_destinations.sql`
adds `mail_begin_nonces`, `mail_destination_attempts`,
`mail_destination_creates`, and `mail_destination_lock`, plus lookup indexes.
No registration columns change. These tables intentionally outlive flow expiry;
create tombstones are not part of periodic flow cleanup.

Operator steps 36-38 name the account var, human-created secret and account-scoped
Email Routing Addresses Write + Read permissions. Omit both
`destination_address` and `allowed_destination_addresses` from `MAIL_SEND`:
Cloudflare restricts that binding to verified destinations and app code enforces
the installation's exact bound owner. Client and Worker must update together;
old unsigned begin callers are refused. All launch gates still apply.

Official references checked 2026-11-11 (documentation only, no live API call):

- [List destinations, pagination and verified field](https://developers.cloudflare.com/api/resources/email_routing/subresources/addresses/methods/list/).
- [Create destinations and Write permission](https://developers.cloudflare.com/api/resources/email_routing/subresources/addresses/methods/create/).
- [Send binding restrictions](https://developers.cloudflare.com/email-service/configuration/send-bindings/).
