-- MC-1166 W03 prerequisite. Additive indexes only; apply after 0005_pull.sql.
-- Reverse indexes bound retention's FK checks and replay/recovery lookups.
-- No runtime migration, table rewrite, authority or retention-policy change.
CREATE INDEX pull_browser_token ON pull_browser_sessions(token_hash);
CREATE INDEX pull_control_change ON pull_control_outbox(change_id,expires_ms);
CREATE INDEX pull_flow_generation_expiry ON pull_flows(install_id,expected_generation,expires_ms);
CREATE INDEX pull_thread_intent ON pull_threads(install_id,generation,intent_id);
CREATE INDEX pull_thread_generation_replay ON pull_threads(install_id,generation,dedupe_until_ms);
