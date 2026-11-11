# DRAFT: agent mail privacy disclosure

**DRAFT ONLY. Not an effective privacy policy or approval to enable the service.**
This describes the disabled MC-1166 implementation candidate. Production
deployment, operational verification and publication are separate gates. The
website and Android store policy have not been changed by this draft.

## Optional service and consent

Agent mail lets an enrolled installation send notes and questions to one verified
owner mailbox and accept replies for the recorded conversation. Setup is optional.
Viewing or declining the setup step sends no address or mail registration; it
does not change the anonymous update-ping choice. Continuing setup fetches a
signed service manifest, then asks for the dashboard passcode before submitting
the address. Enrollment, verification and turning mail on are separate steps.

## Data sent and recipients

| Operation | Data and recipient |
| --- | --- |
| Setup and verification | The Clayrune mail service (Cloudflare Worker and D1) receives the owner email, opaque installation/flow identifiers, mail public key, consent generation, signed enrollment evidence and tunnel route metadata. The enrollment authority checks the account/device/tunnel relationship. Cloudflare handles destination verification mail. |
| Notes and questions | The mail service and email delivery providers process the intended subject/body, project name, conversation label, routing identifiers, message IDs and tokenized Reply-To address. The destination is the verified owner mailbox. Agents can include sensitive project information in the text they send. |
| Incoming replies | Cloudflare processes incoming email and checks sender/thread authentication. MIME stripping selects supported plain text and excludes attachments/HTML. The durable service ledger holds deliverable text and authentication/routing evidence for replay to the enrolled installation through its tunnel. Unsupported mail can be held or refused. |
| Local extraction and continuation | The installation sends the stripped text to Claude for extraction in a tool-free call (currently the Claude/Haiku runtime), separating fresh reply text from quotes and forwards. Ambiguous or instruction-like content is held. Only a validated, unambiguous reply envelope can enter the recorded conversation, whose selected provider then processes that continuation. Normal session transcripts and project memory can retain that accepted reply under existing local settings. |
| Measurement | The service maintains mail registration/activation counters. These identify a different population from the anonymous update ping; no ping ID is submitted by mail enrollment. Aggregate counts exclude owner addresses, message text and reply tokens. |

Mail is identified service data. Cloudflare, the mail-service operator, the
owner's email provider, Claude's provider and the resumed agent's provider can
process relevant data at their respective stages. Network providers receive
connection metadata. Authorized service operators with infrastructure access can
access stored service data; local encryption does not make email end-to-end
encrypted. Processor regions, international-transfer terms and operator access
procedures must be confirmed before publication.

Mail content is for delivery, extraction and the intended conversation. It must
not be reused for advertising, promotional contacts, cross-project memory,
learning artifacts or training datasets. This restriction is a product contract;
provider retention/training terms still require a publication review. Ordinary
session memory behavior within the target project remains applicable.

## Retention and deletion: implementation defaults, pending approval

These are source defaults, **not an approved production retention promise**.
Cleanup occurs when reconciliation runs; an offline installation cannot clean up
on a wall-clock deadline.

| Record | Current implementation |
| --- | --- |
| Worker reply payload | Removed when the installation acknowledges durable custody, or when reconciliation observes revocation/thread expiry or the 30-day queue deadline. |
| Worker routing/event metadata | Threads default to 90 days; terminal event/rejection metadata defaults to 30 days. Expired thread metadata remains for the additional metadata window. Intent cleanup uses the thread plus metadata windows, with in-flight uncertainty handled separately. |
| Local inbox | Queue/hold expiry defaults to 30 days, threads to 90 days and terminal/deduplication metadata to 30 days. Terminal transitions clear content. A submitted event lacking turn-completion evidence expires after the one-day completion window. |
| Ownership and revocation evidence | Compact question ownership records have no age-only eviction; session/project deletion releases them. Service address reservations and cancellation/revocation tombstones prevent stale access and address reassignment. No blanket 30-day deletion claim applies to these records. |
| Existing email and conversation history | Copies in the owner's mailbox and previously created conversation transcripts follow those systems' own deletion controls. |

In **Settings > Connectivity > Agent mail**, use **Disable** and confirm with
the dashboard passcode. A successful local disable blocks local sends and mail
continuations immediately and queues remote revocation. **Disabled here,
revocation pending** means the service has not acknowledged revocation;
**Retry revocation** asks again. Remote revocation is distinct from erasure.
Closing Settings or declining setup later does not disable an existing enrollment.

Deleting a session/project revokes its local bindings and cancels queued local
work; if the store is unavailable, a durable local deletion intent is retried.
This does not delete the installation's service registration or all remote copies.
The current UI has no complete account-wide mail-data erasure control. Before
publication the operator must supply a verified deletion-request channel, the
scope of account/registration deletion, tombstone/aggregate retention and
Cloudflare/model-provider/backup deletion lag. Do not promise immediate removal
of all copies, or treat Disable as deleting earlier messages.

## Publication blockers

- Confirm the actual service operator/contact, processors/regions, access policy
  and provider terms; no operator-specific identity is inserted into source.
- Approve retention/capacity values and a complete service-side erasure process,
  including exceptions and backup lag. The table above records current code only.
- Verify actual deployed behavior, consent text and model processing against this
  disclosure, then review the public privacy policy and store declarations in a
  separately authorized publication step.

## Source anchors for review

- Enrollment and consent: `mc/agent_mail_enrollment.py`,
  `mc/agent_mail_ceremony_client.py`, `static/js/agent-mail-setup.js`.
- Outbound content: `mc/agent_mail_outbound.py`; ingress/retention:
  `workers/agent-mail/ingress.js`, `strip.js`, `ledger.js` and `replay.js`.
- Local extraction/storage: `mc/agent_mail_extract.py`, `mc/mail_launder.py`,
  `mc/agent_mail_store.py`, `mc/agent_mail_ownership.py` and `mc/agent_mail_deletion.py`.
- Contract: `docs/AGENT_MAIL_SPEC.md`, Data, privacy and deletion contract.
