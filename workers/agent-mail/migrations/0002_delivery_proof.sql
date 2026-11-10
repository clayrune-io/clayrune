-- Apply AFTER 0001_enrollment.sql. 7d-2c proof of machine custody (contract section 4.5).
-- One row per install+generation. 'armed' carries the challenge the Worker will embed (stored by the
-- lifecycle proof-arm call, 7d-2d); 'verified' keeps only its digest. 'consumed' is final: a proof
-- that activated a generation can never be re-armed or reused. Proof rows never grant delivery:
-- only registrations.active/route_verified (set by the lifecycle activate call) do.
CREATE TABLE mail_delivery_proofs (
  install_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation > 0),
  challenge TEXT, challenge_sha256 TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('armed','verified','invalidated','consumed')),
  armed_ms INTEGER NOT NULL, armed_expires_ms INTEGER NOT NULL,
  receipt_digest TEXT, verified_ms INTEGER, expires_ms INTEGER,
  PRIMARY KEY(install_id, generation)
);
