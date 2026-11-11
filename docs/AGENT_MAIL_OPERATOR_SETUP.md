# Agent-mail operator setup (MC-1166)

Windows first: use **Git Bash**, not PowerShell or WSL, for every command below.
Commands start in your Mission Control checkout; later commands explicitly change
directories. Run this yourself. Nothing here authorizes an agent to create real
keys, contact Cloudflare, change this install, deploy, or enable mail.

**Status: preparation runbook, not a completed live-service recipe.** The source
supports the offline key/signing commands. Cloudflare CLI flags, dashboard labels,
account entitlements, DNS and provider delivery are **unverified** here: this unit
made no network calls and did not run Wrangler. Steps marked **blocked** cannot
be completed with the current UI/API. Do not treat a deployed Worker as working
agent mail.

## Before you start

Decide and record these before step 1 (public information only):

| Decision | What you need |
| --- | --- |
| Cloudflare account and zone | Account you administer, its account ID, and the DNS zone you may change. Confirm capacity/costs before creating resources. |
| Mail domain and sender | Dedicated mail domain/subdomain, sender such as `agent@YOUR_MAIL_DOMAIN`, approved owner destination(s). Changing existing MX can interrupt other mail; do not replace working mail routing casually. |
| Service identity | Worker name (template: `clayrune-agent-mail`); canonical public HTTPS origin, either its workers.dev origin or a custom hostname you control. No path, port, trailing slash, query or redirect. This is separate from the reply mail domain and from the control-plane origin. |
| Root custody | Offline machine/removable storage and backup/recovery custody. Only the root PUBLIC half leaves the offline signing environment; do not sync the root private file or upload it to Cloudflare. |
| Service key | Unique key ID (template: `mail-service-v1`); service private file may be transferred to the online publishing machine, public half to the offline signer. The root and service are separate keys. |
| Manifest policy | Initial sequence `1` only for a genuinely new service; otherwise greater than every previously published sequence. Choose validity days, key retirement horizon (at least validity), renewal owner and reminder date. No schedule is created by this kit. |
| Launch approval | Who signs off the evidence below, publishes disclosures and authorizes deployment/real pilot mail. Deployment and enablement are separate actions. |

The following evidence remains **unverified/unmet by this kit**, even where code
and synthetic tests exist. These are the launch gates from
[the transport contract](AGENT_MAIL_TRANSPORT.md#synthetic-validation-and-outstanding-launch-gates):

- Actual Cloudflare runtime, `MAIL_DB`/`MAIL_SEND` bindings, capacity, namespace,
  domain ownership and DNS/DKIM; approved and verified sender/destinations.
- Real provider ingress/authentication headers and their positional provenance.
  `MAIL_INGRESS_VERIFIED=true` records a reviewed result; flipping it is not evidence.
- Real enrollment and tunnel ownership proof, including deployed control-plane
  authority/JWKS/service authentication and mail-only ingress capability. The two
  keys generated here are not the control plane's signing identity. Do not replace
  this with an app-wide Access token or loosen dashboard authentication.
- Signed receiver reachable through the actual tunnel, durable local custody,
  restart/reconnect remote reconciliation, consent/revocation/deletion/key lifecycle,
  laundering and exact-session durable follow-up plus a completed resumed turn.
- Retention/deletion and cleanup scheduling, monitoring of custody failures,
  quotas/storage/capacity, and approved policy for the provisional 30-day queued
  text / 90-day threads / 30-day terminal metadata defaults. Provider retry is not
  proven lossless; both scheduled replay and install recovery are required.
- Published privacy disclosures, real destination verification, real mail and a
  supervised pilot, each separately human-approved. Draft disclosures are not publication.

There are also two concrete product/configuration blockers:

1. The two public deployment settings have **no writable UI/API** in this source.
   `mc/agent_mail_factory.py` reads `agent_mail_root_public_key` and
   `agent_mail_service_origin`, but `mc/blueprints/settings_routes.py` omits both
   from `_CONFIG_EDITABLE_KEYS`. `PUT /api/config` ignores unknown keys, potentially
   returning HTTP 200 with `updated: []`; GET omits them too. No guessed curl or
   Settings path appears in this guide. The Clayrune configuration step stops at this gap.
2. Publishing a manifest is not enrollment configuration. The template lacks
   reviewed control-plane/enrollment settings; the local factory also requires
   `agent_mail_install_id`, real adapters/prerequisites and human enablement.
   These must be supplied by the owning integration/release work, not invented
   here. See `workers/agent-mail/enrollment-http.js`, `lifecycle.js`,
   `mc/agent_mail_factory.py` and [the enrollment contract](AGENT_MAIL_UNIT7D2E_CONTRACT.md).

Keep both `MAIL_ENABLED` and `MAIL_INGRESS_VERIFIED` at `"false"` throughout
preparation. Even `/v1/mail/manifest` returns `503 mail_disabled` while
`MAIL_ENABLED` is false (`worker.js` checks it before the manifest route).

## Prepare the tools and keys

On the offline signing machine, preinstall Python plus this checkout's existing
`cryptography` dependency before disconnecting it. A normal developer checkout's
`.venv` already supplies them. Do not install dependencies while handling the root
key. On the online publishing machine, have Git for Windows (Git Bash) and Node
24.11+ with npm installed. Installer click paths/versions are **unverified** here;
use the official [Git](https://git-scm.com/downloads/win) and
[Node](https://nodejs.org/en/download) installers if these are missing.

Each numbered item is one action. Stop on an error; the “See” line is the expected
result, not evidence that this kit performed the action. Substitute your own
checkout path in step 2, then reuse `$REPO` on that machine. Repeat steps 1–2 on
the offline machine as needed. All variables below are public values or file paths.

1. Open **Start > Git Bash**. See a shell prompt.

2. Enter your checkout path (replace the example path), saving it for later:

   ```bash
   REPO="$(cd '/c/path/to/mission-control' && pwd)" && cd "$REPO"
   ```

   See the checkout as your current folder; `&&` stops if the path does not exist.

3. Check the offline Python dependency:

   ```bash
   .venv/Scripts/python.exe -c 'from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; print("Ed25519 available")'
   ```

   See `Ed25519 available`. Use this interpreter for all Python commands below.

4. On the **online** publishing machine, install Wrangler (**unverified**, network/install):

   ```bash
   npm install --global wrangler
   ```

   See npm complete without errors. This guide uses the installed `wrangler` command.

5. Authenticate that machine to your chosen account (**unverified**, network/login):

   ```bash
   wrangler login
   ```

   See Cloudflare's browser consent and Wrangler's successful login message.

6. On the signing machine, disconnect networking using **Windows Settings > Network & internet > Wi-Fi > Off** (or physically disconnect Ethernet). See no active network connection. Keep it disconnected through signing.

7. On that **offline** machine, generate the two keys into the default outside-repo directory:

   ```bash
   .venv/Scripts/python.exe tools/agent-mail-keygen.py
   ```

   See two PUBLIC keys and two next commands, never private key bytes. Files are
   `$HOME/.clayrune/agent-mail-keys/{root.key,root.pub,service.key,service.pub}`.
   For another location use `--directory '/c/YOUR_OFFLINE_KEY_DIRECTORY'` and adapt
   subsequent paths. Any existing output file aborts the entire preflight; no
   overwrite/force option exists. Never delete an existing key just to rerun this.
   POSIX creates files 0600; Windows creates them with a protected owner-only
   DACL before writing. Use a permissions-capable filesystem (NTFS on Windows).
   A write failure may leave partial files: inspect them, do not blindly retry.

8. Back up the offline root to your approved offline medium using **File Explorer > root.key > Copy > approved offline backup folder > Paste**. See a backup on that medium. Keep it offline and access-controlled; public files alone cannot recover a root key.

9. Transfer **only** `service.key`, `service.pub` and `root.pub` to `$HOME/.clayrune/agent-mail-publish/` on the online machine, using your approved removable medium and File Explorer. See those three files there, with no `root.key`. The service private file needs the same restricted access; never paste its contents into chat, a command, TOML, or the dashboard.

## Create a disabled Worker

10. On the online machine, enter the Worker folder:

    ```bash
    cd "$REPO/workers/agent-mail"
    ```

    See the `workers/agent-mail` directory as the current folder.

11. Install its pinned dependencies (network, npm behavior **unverified** here):

    ```bash
    npm ci
    ```

    See a successful install. The existing lockfile controls dependencies.

12. Create your operator TOML without replacing an existing one:

    ```bash
    (set -o noclobber; cat wrangler.toml.example > wrangler.toml)
    ```

    See no output. An existing `wrangler.toml` makes this fail; open/review it rather
    than overwrite it. The operator copy is gitignored.

13. Open that file:

    ```bash
    notepad.exe wrangler.toml
    ```

    See the template in Notepad. Keep this editor open for the following separate edits.

14. In Notepad, add `account_id = "YOUR_ACCOUNT_ID"` **above `[vars]`**, then save. See a top-level account ID, not a var. Cloudflare interpretation **unverified**.

15. In Notepad, replace `MAIL_DOMAIN` with your chosen mail domain, then save. See the domain only, without `https://`.

16. In Notepad, replace `MAIL_FROM` with your approved sender address, then save. See the sender under that domain.

17. In Notepad, set `MAIL_SERVICE_KEY_ID` to the ID you recorded before starting, then save. See the same ID you will pass as `--key-id`; leave both enable flags `"false"`.

18. Create the dedicated empty D1 database (**unverified**, network/resource creation):

    ```bash
    wrangler d1 create agent-mail
    ```

    See a `database_id` UUID. If a database already exists, stop and have its owner
    review it; do not apply this candidate schema to populated production data.

19. In Notepad, replace **only** `database_id = "REPLACE_ONLY_AFTER_DEPLOY_APPROVAL"` with the returned UUID, then save. See `binding = "MAIL_DB"` and `database_name = "agent-mail"` unchanged.

20. Apply the schema to the new remote database (**unverified**, network/write):

    ```bash
    wrangler d1 execute agent-mail --remote --file=./schema.sql
    ```

    See successful execution of the schema, not a local-only database result.

21. Bootstrap the disabled Worker (**unverified**, network/deploy; requires your explicit approval):

    ```bash
    wrangler deploy
    ```

    See its HTTPS Worker URL. This creates the Worker before secret upload and
    lets you record its exact workers.dev origin. If using a custom service
    hostname, the alternative click path after creation is **Cloudflare dashboard >
    Workers & Pages > clayrune-agent-mail > Settings > Domains & Routes > Add >
    Custom domain** (**unverified**). Complete DNS/certificate activation before
    signing an origin; do not sign a guessed hostname. Keep the service disabled.

22. Upload the service private file through stdin (**unverified**, network/secret creation):

    ```bash
    wrangler secret put MAIL_SERVICE_PRIVATE_KEY < "$HOME/.clayrune/agent-mail-publish/service.key"
    ```

    See secret upload success. The value is not an argv argument or shell history
    text. Do not use `echo`, `cat` alone, or a command substitution that exposes
    the value. `service.key` is unpadded base64url PKCS#8 DER, which matches
    `signing.js`'s `crypto.subtle.importKey('pkcs8', ...)` contract.

## Sign and publish the manifest

Return to the disconnected signing machine, in `$REPO`. Set public inputs there.
The service origin is the Worker HTTP origin, not `MAIL_DOMAIN` and not the CP origin.

23. Enter the exact service origin determined in step 21:

    ```bash
    read -r -p 'Service origin (https://host, no slash): ' MAIL_ORIGIN
    ```

    See your typed public origin.

24. Enter the service key ID saved in step 17:

    ```bash
    read -r -p 'MAIL_SERVICE_KEY_ID: ' MAIL_KEY_ID
    ```

    See the same public ID as the Worker var.

25. Enter the approved sequence:

    ```bash
    read -r -p 'Manifest sequence (1 for new service, otherwise previous + 1): ' MANIFEST_SEQUENCE
    ```

    See a positive integer. The signer validates syntax, not publication history;
    it cannot know if you reused a sequence. Never change bytes at an old sequence.

26. Enter your approved validity and retirement horizons, in days from issuance:

    ```bash
    read -r -p 'Validity days, then service-key retirement days (space separated): ' VALID_DAYS RETIRE_DAYS
    ```

    See two positive integers, retirement at least validity. Choose the policy
    before this step; neither horizon is defaulted by the signer.

27. Compute exact UTC timestamps from this machine's correct clock:

    ```bash
    TIMES="$(.venv/Scripts/python.exe - "$VALID_DAYS" "$RETIRE_DAYS" <<'PY'
    import datetime as dt, sys
    validity, retirement = map(int, sys.argv[1:])
    assert 0 < validity <= retirement, 'retirement must be at least validity, both positive'
    issued = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    moments = [issued, issued + dt.timedelta(days=validity), issued + dt.timedelta(days=retirement)]
    print(' '.join(moment.strftime('%Y-%m-%dT%H:%M:%SZ') for moment in moments))
    PY
    )" && read -r ISSUED EXPIRES RETIRE <<< "$TIMES"
    ```

    See no output on success; invalid inputs produce an error: stop if that happens.
    `--activate` will equal issuance; the verifier requires issued ≤ now < expires ≤ retire.
    ISO timestamps must end in `Z`. Confirm the offline clock before signing.

28. Sign into a new sequence-named public file outside the checkout:

    ```bash
    .venv/Scripts/python.exe tools/agent-mail-manifest-sign.py --root-key-file "$HOME/.clayrune/agent-mail-keys/root.key" --origin "$MAIL_ORIGIN" --sequence "$MANIFEST_SEQUENCE" --issued "$ISSUED" --activate "$ISSUED" --expires "$EXPIRES" --retire "$RETIRE" --key-id "$MAIL_KEY_ID" --service-public-key="$(tr -d '\r\n' < "$HOME/.clayrune/agent-mail-keys/service.pub")" --out "$HOME/.clayrune/agent-mail-keys/manifest-$MANIFEST_SEQUENCE.json"
    ```

    See `signed manifest sequence ...` and root PUBLIC key on stderr. The output
    is compact `{"manifest":[...],"signature":"..."}` JSON; the signer re-verifies
    it before writing and refuses an existing output unless explicitly forced.
    Equals form for `--service-public-key=...` also handles public keys starting
    with `-`. No private file leaves the offline signing environment.

29. Transfer **only** `manifest-N.json` to the online machine's `$HOME/.clayrune/agent-mail-publish/`, using File Explorer and your approved removable medium. See that public file there; leave `root.key` offline.

30. On the online machine, set the var in the existing `[vars]` section (enter the sequence you just signed):

    ```bash
    read -r -p 'Signed sequence: ' MANIFEST_SEQUENCE
    ```

    See the same integer as step 25. Then the next action is:

31. Insert/update the public manifest in the operator TOML:

    ```bash
    "$REPO/.venv/Scripts/python.exe" - "$HOME/.clayrune/agent-mail-publish/manifest-$MANIFEST_SEQUENCE.json" "$REPO/workers/agent-mail/wrangler.toml" <<'PY'
    import json, pathlib, re, sys
    doc = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8').strip()
    assert set(json.loads(doc)) == {'manifest', 'signature'}
    path = pathlib.Path(sys.argv[2])
    text = path.read_text(encoding='utf-8')
    text = re.sub(r'^MAIL_SERVICE_MANIFEST\s*=.*\n?', '', text, flags=re.M)
    assert text.count('[vars]\n') == 1
    text = text.replace('[vars]\n', '[vars]\nMAIL_SERVICE_MANIFEST = ' + json.dumps(doc) + '\n', 1)
    path.write_text(text, encoding='utf-8', newline='\n')
    print('Public manifest set in [vars]; enable flags unchanged')
    PY
    ```

    See `Public manifest set in [vars]; enable flags unchanged`. This assumes the
    template's single-line var layout; do not use it on a hand-written multiline
    manifest. `MAIL_SERVICE_MANIFEST` is public, not a secret. Cloudflare TOML
    consumption is **unverified**; the JSON string encoding is checked locally.

32. Deploy the still-disabled signed configuration (**unverified**, network/deploy):

    ```bash
    cd "$REPO/workers/agent-mail" && wrangler deploy
    ```

    See a successful deployment/version; flags remain false. Every later
    deployment must preserve the public manifest var in this TOML; dashboard-only
    edits can otherwise be replaced by the next deploy.

33. Check the disabled route (**unverified**, network):

    ```bash
    read -r -p 'Exact service origin: ' MAIL_ORIGIN
    ```

    See the origin from step 23. Then perform the next action:

34. Request the manifest without following redirects (**unverified**, network):

    ```bash
    curl --max-time 10 -i "$MAIL_ORIGIN/v1/mail/manifest"
    ```

    See HTTP **503** and `{"code":"mail_disabled"}`. This is the disabled
    checkpoint, not signature validation or evidence the var is valid. A redirect
    or a login page is not success; Clayrune will refuse redirects.

## Configure the provider (human-approved DNS/mail actions only)

These Cloudflare click paths and binding fields are **unverified** against a live
account/current Wrangler. Use the domain/account recorded before starting. If a
label or entitlement differs, stop and verify the provider procedure; do not
change another domain or substitute a different mail service silently. Keep the
Worker disabled; no test email is sent by these preparation instructions.

35. In **Cloudflare dashboard > chosen zone > Email > Email Routing > Settings >
    Enable Email Routing**, review/apply the provider's required DNS records only
    after approving the MX impact. See the chosen mail domain/subdomain reported
    active with required mail-authentication/DNS records. If subdomain routing is
    unavailable in your account, stop for a domain decision. Do not replace
    unrelated working MX. This does not prove the sender's DNS/DKIM is correct;
    provider verification and a later authorized real-delivery pilot must prove it.

36. In **Email Routing > Destination addresses > Add destination address**, enter
    the approved owner mailbox and request verification. See a pending destination;
    this sends provider verification mail and therefore needs human authorization.

37. In that mailbox, open the Cloudflare verification message and click **Verify
    email address**. See the chosen address marked verified in Cloudflare. Verify
    the sender/domain through the provider's current sender setup too; a verified
    destination alone is not sender authentication or owner enrollment.

38. In the operator TOML's `[[send_email]]` block, add
    `allowed_destination_addresses = ["YOUR_VERIFIED_OWNER_ADDRESS"]` and save.
    See `name = "MAIL_SEND"` unchanged and destinations restricted to the approved
    mailbox(es). The field is **unverified** against Wrangler here; verify its
    current schema before deployment. Do not put this operator file in Git. The
    Worker still derives each recipient from its own verified owner registration;
    this binding does not supply consent or owner enrollment. Apply the change
    with the next approved deployment, not an unrecorded dashboard-only edit.

39. In **Email Routing > Routing rules > Catch-all address > Edit**, select
    **Send to a Worker > clayrune-agent-mail > Save** for the chosen dedicated mail
    namespace. See the Worker as that namespace's catch-all destination, so
    `install+token@MAIL_DOMAIN` addresses route there. Do not route an unrelated
    mailbox namespace or send a real pilot reply yet. The disabled Worker rejects
    inbound mail, and the ingress provenance/pilot gates must pass before normal
    mail may be routed to an enabled handler.

## Configure Clayrune and enable only after evidence

40. **BLOCKED: set the two Clayrune public deployment values.** No supported click
    path or writable API exists in this source; stop here and request the missing
    human/passcode-gated configuration surface. Required values are exactly:

    ```text
    agent_mail_root_public_key = contents of root.pub (43-character base64url public key)
    agent_mail_service_origin = exact origin signed in manifest[2]
    ```

    Expected result after that separate work: readback of both persisted values,
    not merely HTTP 200. Do not put `root.key` or `service.key` into Clayrune config.
    Do not use `PUT /api/config` for these keys: it ignores them today. Do not
    manually bypass the settings gate or turn feature flags on to hide this gap.

41. **BLOCKED until launch gates are signed off:** have the integration owner
    supply/review all CP, service enrollment and local prerequisites described
    above. Expected result: recorded evidence for every gate, including a
    controlled pilot plan. The two config values alone are insufficient.

42. **Only after explicit approval of those gates**, use Notepad to change
    `MAIL_ENABLED = "false"` to `MAIL_ENABLED = "true"` in the operator TOML and
    save. Expected result: that single circuit-breaker change; ingress remains
    disabled until its own provider-fixture evidence permits activation.

43. Deploy the approved configuration (**unverified**, network/deploy):

    ```bash
    cd "$REPO/workers/agent-mail" && wrangler deploy
    ```

    See a successful deployment. If this is not authorized, stop at the disabled
    checkpoint; there is no manifest-only enable switch in the existing Worker.

44. Fetch the now-enabled manifest (**unverified**, network):

    ```bash
    curl --max-time 10 -i "$MAIL_ORIGIN/v1/mail/manifest"
    ```

    See **200**, JSON content type, `cache-control: no-store` and exactly the
    signed document. `503 manifest_not_configured` means the var is missing;
    `503 manifest_malformed` means its shape/size is refused. A 200 is only a
    shape check: the Worker does not verify the root signature or freshness.

45. With enrollment integration separately authorized, open **Clayrune > Settings
    > Connectivity > Agent mail**. See the existing status/Controls card; use
    its human passcode flow for enrollment. If unavailable/off or reporting
    `not_configured`, `deployment_unconfigured`, missing proof or prerequisites,
    stop: this guide does not supply those missing integrations. Before any
    real inbound pilot, `MAIL_INGRESS_VERIFIED` needs its own reviewed provider
    evidence and approved deployment; do not set it on the strength of a manifest GET.

## Renew before expiry

46. At the recorded reminder date, repeat steps 23–29 offline with the **same
    root**, the still-current service public key/ID, a strictly higher sequence
    and newly approved timestamps. See a new signed `manifest-N.json`; this is
    manifest renewal, not key rotation. Do not rerun keygen or reset any rollback floor.

47. Repeat steps 30–32 online to replace the var and deploy, preserving the
    already-reviewed enable flags. See a successful new deployment.

48. Repeat step 44 while the old manifest is still valid. See the new sequence
    at `manifest[3]` and future expiry milliseconds at `manifest[5]`; the existing
    Clayrune manifest consumer must also accept it against its pinned root.
    Expired/not-yet-valid or rolled-back documents are refused; a changed
    document cannot reuse an old sequence. Do not “fix” expiry by clearing state.

Service-key rotation is separate: create/review new key material, sign the new
public key/ID and retired-key list, then use the existing deliberate repinning
ceremony. Root replacement needs an explicit trust-distribution decision; never
silently replace the pinned root. To stop mail, the operator circuit breaker is
`MAIL_ENABLED = "false"` followed by an approved deployment; preserve the keys,
database and tombstones for investigation/recovery rather than deleting them.

## Local verification of this runbook

- Keygen flags: `tools/agent-mail-keygen.py::_parser`; signer flags/time parsing:
  `tools/agent-mail-manifest-sign.py::_parser/parse_time_ms`; payload/freshness/
  rollback: `mc/agent_mail_service_manifest.py::verify_service_manifest`.
- Origin/root names: `mc/agent_mail_factory.py::ROOT_KEY/ORIGIN_KEY`; write-path
  blocker: `mc/blueprints/settings_routes.py::_CONFIG_EDITABLE_KEYS/update_config`.
- GET path/errors/size/redirect policy: `mc/agent_mail_manifest_fetch.py`,
  `workers/agent-mail/manifest-http.js`; global gate: `worker.js::fetch`.
- D1 binding/schema, service key ID/domain/from, send binding and replay cron:
  `workers/agent-mail/wrangler.toml.example`, `schema.sql`, `signing.js`.
- All Wrangler commands/dashboard steps above remain **unverified**; no CLI
  version, entitlement, provider behavior or successful live deployment is claimed.
