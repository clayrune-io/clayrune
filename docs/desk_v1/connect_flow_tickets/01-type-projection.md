# MC-1062 / 01: Project connection types, not route cards

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

## Depends on

Q1 recorded by Dave.

## Ownership

New `mc/desk_connect/type_view.py`; new read-only blueprint `mc/blueprints/desk_connect_type_routes.py`; focused `tests/test_desk_connect_types.py`. Registry/blueprint registration contains data and wiring only.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Join existing profile routes, setup providers and custom-connection variants into Sign in, API and MCP types. Keep route IDs and service/account-kind evidence. Expose setup support separately from runtime execution. Browser routes must not depend on the old connect_method projection. Do not mark unavailable providers available or edit the original source profiles just to make a button appear. Provide reference-only and missing-adapter explanations for Details.

## Acceptance

X has browser and OAuth setup paths in the new contract; LinkedIn has member/Page browser setup and truthful unavailable API status. Custom npm/remote appear without catalogue membership. Unknown/API/PyPI detection never implies execution. Pure fixtures, no vault values or network. Existing inspect clients stay compatible.

## Boundary

Small read-only slice. It does not create accounts, change permissions or make a new provider work.
