# MC-1062 / 12b: retain reference-only connection drafts

Dave chose option A in the item journal: implement this backend prerequisite
before [ticket 12](12-unknown-service.md). No wizard activation, executor or probe.

## Contract

The existing `POST /api/desk/connect/commit` accepts `reference_draft` only on
`method: save_for_agents`. It keeps the existing unattended refusal, shape check
before passcode, human/passcode check, process-local request-id replay, create-only
name guard and compensating rollback. No new route or authorization path exists.

`reference_draft` is exactly `{kind, fields}`. Kind is `api_base`, `api_spec` or
`pypi`. Every field is `{value, provenance, confidence}` using U1's enumerations.
Allowed fields: public HTTPS `address` (300 characters), PyPI `package`
(name or name==version, 200 characters), enumerated `auth_type` and `transport`,
`credential_names` and `placements` (12 entries), `scopes` (20 entries).
List values are at most 120 characters. API requires address; PyPI requires package.
Unknown fields, wrong-kind fields, hidden characters and credential-like literals
are refused. No raw specification/README, command, argument, value, approval,
permission or executable configuration is retained.

Stored/returned metadata has fixed schema `desk-reference-draft/1`, an untrusted
data label, and `approved:false`, `executable:false`, `publish:false`. It lives
inside the existing workspace service record in `data/desk.json`, outside
`DATA_DIR`. List/create/update responses preserve the metadata; metadata is
create-only, and old records retain their exact public shape. Saved-service records
are never accounts or registered MCP servers.

An existing credential is exactly `{name, existing:true}`. Validation and Save
check vault metadata for that name, refuse internal/missing entries, never resolve
a value, replace an entry or modify its scope/unattended policy. Result says
`stored:false, referenced:true`. Typed credentials keep the existing contract and
are stored only by final Save. Rollback deletes only a credential this request
created, never a referenced entry; orphan cleanup remains explicit if it fails.

## Implementation and rollback

`reference_draft.py` owns validation; `reference_save.py` owns the moved local
Save body. `commit.py` dispatches and retains its replay lock; `desk_services.py`
has narrow create/serialization wiring. No network, discovery, installer, account,
permission, vault-read or check execution was added. Revert these changes to refuse
new metadata inputs; previously saved metadata must be retained as user data.

## Validation

`tests/test_desk_connect_reference_draft.py` exercises real route/passcode, temp
vault/store, metadata roundtrip, old shape, replay/conflict, no-account PyPI,
hostile labelled data, invalid inputs before passcode, unattended/wrong-passcode,
reference-policy preservation, deletion race, create-only and rollback/retry.
Final command/results recorded below at the checkpoint.

Checkpoint: `python -m pytest tests/test_desk_connect_reference_draft.py tests/test_desk_connect.py tests/test_desk_services.py -o addopts='' -q`: **111 passed in 10.68s**, exit 0. Basic pyright on reference_draft, reference_save, commit and desk_services: **0 errors, 0 warnings, 0 informations**. Legacy response regression was caught and corrected before the checkpoint. Wizard remains disabled.
