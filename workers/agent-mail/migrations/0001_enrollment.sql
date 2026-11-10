-- Apply AFTER schema.sql. New concern, no positional changes to transport tables.
-- Never infer activation from these ownership records. Only proven identities
-- survive expiry/revocation. Unproved reservations have a bounded lifetime.
CREATE TABLE mail_identity_reservations (
  install_id TEXT PRIMARY KEY, device_id TEXT NOT NULL UNIQUE,
  address_local TEXT NOT NULL UNIQUE CHECK(length(address_local)=30),
  expires_at INTEGER NOT NULL
);
CREATE TABLE mail_installations (
  install_id TEXT PRIMARY KEY, device_id TEXT NOT NULL UNIQUE,
  address_local TEXT NOT NULL UNIQUE CHECK(length(address_local)=30),
  generation INTEGER NOT NULL DEFAULT 1 CHECK(generation>0),
  ownership_checked_at INTEGER, ownership_lease_until INTEGER, record_version TEXT
);
CREATE TABLE mail_enrollment_flows (
  flow_id TEXT PRIMARY KEY, flow_digest TEXT NOT NULL,
  -- Can name an expiring reservation or a permanent installation. Historical
  -- flows remain after an unproved reservation expires; commit checks both.
  install_id TEXT NOT NULL,
  expected_generation INTEGER NOT NULL, challenge_id TEXT NOT NULL UNIQUE,
  transcript TEXT NOT NULL, r_digest TEXT NOT NULL, owner_address TEXT NOT NULL,
  code_digest TEXT NOT NULL, code_failures INTEGER NOT NULL DEFAULT 0 CHECK(code_failures BETWEEN 0 AND 5),
  code_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(code_confirmed IN (0,1)),
  provider_checked_at INTEGER, expires_at INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','invalidated','consumed'))
);
CREATE UNIQUE INDEX mail_one_pending_generation ON mail_enrollment_flows(install_id,expected_generation) WHERE state='pending';
CREATE TABLE mail_enrollment_commits (
  jti TEXT PRIMARY KEY, flow_id TEXT NOT NULL UNIQUE REFERENCES mail_enrollment_flows(flow_id),
  r_digest TEXT NOT NULL, token_digest TEXT NOT NULL, expected_generation INTEGER NOT NULL,
  checked_at INTEGER NOT NULL, lease_until INTEGER NOT NULL, record_version TEXT NOT NULL,
  admitted_at INTEGER NOT NULL, grant_expires_at INTEGER NOT NULL
);
-- One statement owns all effects. Constraints/trigger failures roll back the
-- grant claim, challenge consume, generation CAS and inactive registration.
-- This deliberately does not depend on D1 batch changes() propagation.
CREATE TRIGGER mail_enrollment_check BEFORE INSERT ON mail_enrollment_commits BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM mail_enrollment_flows f JOIN (
      SELECT install_id,generation,NULL AS expires_at FROM mail_installations
      UNION ALL SELECT install_id,0,expires_at FROM mail_identity_reservations
    ) i ON i.install_id=f.install_id
    WHERE f.flow_id=NEW.flow_id AND f.r_digest=NEW.r_digest
      AND f.expected_generation=NEW.expected_generation AND i.generation=NEW.expected_generation
      AND (i.generation>0 OR i.expires_at>NEW.admitted_at)
      AND f.state='pending' AND f.code_confirmed=1 AND f.code_failures<5
      AND f.expires_at>NEW.admitted_at AND f.provider_checked_at<=NEW.admitted_at
      AND f.provider_checked_at>NEW.admitted_at-60000
      AND NEW.grant_expires_at>NEW.admitted_at AND NEW.checked_at<=NEW.admitted_at
      AND NEW.lease_until=NEW.checked_at+300000 AND NEW.lease_until>NEW.admitted_at
      AND NOT EXISTS (SELECT 1 FROM registrations r WHERE r.install_id=i.install_id
        AND (r.generation!=NEW.expected_generation OR r.active!=0))
  ) THEN RAISE(ABORT,'enrollment_cas_refused') END;
END;
CREATE TRIGGER mail_enrollment_apply AFTER INSERT ON mail_enrollment_commits BEGIN
  -- This runs only after dual proofs, authority and mailbox checks. Promotion,
  -- generation CAS, registration and consume roll back together on any fault.
  INSERT INTO mail_installations(install_id,device_id,address_local)
    SELECT p.install_id,p.device_id,p.address_local FROM mail_identity_reservations p
    JOIN mail_enrollment_flows f ON f.install_id=p.install_id WHERE f.flow_id=NEW.flow_id;
  DELETE FROM mail_identity_reservations
    WHERE install_id=(SELECT install_id FROM mail_enrollment_flows WHERE flow_id=NEW.flow_id);
  UPDATE mail_installations SET generation=NEW.expected_generation+1,
    ownership_checked_at=NEW.checked_at, ownership_lease_until=NEW.lease_until, record_version=NEW.record_version
    WHERE install_id=(SELECT install_id FROM mail_enrollment_flows WHERE flow_id=NEW.flow_id);
  INSERT INTO registrations(install_id,generation,active,owner_verified,owner_address,address_local,
    key_id,public_key,route_origin,route_path,route_generation,route_verified)
    SELECT i.install_id,i.generation,0,1,f.owner_address,i.address_local,
      json_extract(f.transcript,'$[11]'),json_extract(f.transcript,'$[12]'),
      json_extract(f.transcript,'$[15]'),json_extract(f.transcript,'$[16]'),i.generation,0
    FROM mail_enrollment_flows f JOIN mail_installations i ON i.install_id=f.install_id WHERE f.flow_id=NEW.flow_id
    ON CONFLICT(install_id) DO UPDATE SET generation=excluded.generation,active=0,owner_verified=1,
      owner_address=excluded.owner_address,key_id=excluded.key_id,public_key=excluded.public_key,
      route_origin=excluded.route_origin,route_path=excluded.route_path,route_generation=excluded.route_generation,route_verified=0;
  UPDATE mail_enrollment_flows SET state='consumed',code_digest='' WHERE flow_id=NEW.flow_id;
END;
