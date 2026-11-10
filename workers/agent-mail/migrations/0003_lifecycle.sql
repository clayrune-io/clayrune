-- Apply AFTER 0001_enrollment.sql (0002 is independent). MC-1166 7d-2d remote lifecycle, contract 5.2.
-- Fences are durable tombstones: a cancelled or revoked flow can never commit, and the answer to
-- "what was assigned" never changes once written. They are never purged.
ALTER TABLE mail_enrollment_flows ADD COLUMN fence_digest TEXT NOT NULL DEFAULT '';
CREATE TABLE mail_lifecycle_fences (
  flow_id TEXT PRIMARY KEY, fence_digest TEXT NOT NULL CHECK(length(fence_digest)=64),
  -- The assignment this flow had when it was fenced (expected_generation + 1); NULL = none ever existed.
  install_id TEXT, generation INTEGER, created_ms INTEGER NOT NULL
);
CREATE INDEX mail_lifecycle_fences_install ON mail_lifecycle_fences(install_id, generation);
-- Worker -> CP revoke is retried from cron; Worker deactivation (the thing that stops delivery) comes first.
CREATE TABLE mail_lifecycle_cp_revokes (
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, created_ms INTEGER NOT NULL,
  done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0,1)), PRIMARY KEY(install_id, generation)
);
-- Same check as 0001 plus: a fenced flow can never commit. The fence read happens inside the single
-- INSERT statement, so a cancel that lands between the JS-side checks and the commit still wins.
DROP TRIGGER mail_enrollment_check;
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
      AND NOT EXISTS (SELECT 1 FROM mail_lifecycle_fences lf WHERE lf.flow_id=NEW.flow_id)
      AND NOT EXISTS (SELECT 1 FROM registrations r WHERE r.install_id=i.install_id
        AND (r.generation!=NEW.expected_generation OR r.active!=0))
  ) THEN RAISE(ABORT,'enrollment_cas_refused') END;
END;
