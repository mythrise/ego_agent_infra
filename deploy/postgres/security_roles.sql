-- Run as the database owner, then map these NOLOGIN group roles to platform-managed
-- LOGIN identities. No password is stored here.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'egoagentos_runtime') THEN
        CREATE ROLE egoagentos_runtime NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'egoagentos_auditor') THEN
        CREATE ROLE egoagentos_auditor NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'egoagentos_evidence_writer') THEN
        CREATE ROLE egoagentos_evidence_writer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'egoagentos_memory_curator') THEN
        CREATE ROLE egoagentos_memory_curator NOLOGIN;
    END IF;
END
$$;

ALTER ROLE egoagentos_runtime
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE egoagentos_auditor
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE egoagentos_evidence_writer
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE egoagentos_memory_curator
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

DO $$
BEGIN
    EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO egoagentos_runtime, egoagentos_auditor, '
        || 'egoagentos_evidence_writer, egoagentos_memory_curator',
        current_database()
    );
END
$$;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO
    egoagentos_runtime,
    egoagentos_auditor,
    egoagentos_evidence_writer,
    egoagentos_memory_curator;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM
    egoagentos_runtime,
    egoagentos_auditor,
    egoagentos_evidence_writer,
    egoagentos_memory_curator;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM
    egoagentos_runtime,
    egoagentos_auditor,
    egoagentos_evidence_writer,
    egoagentos_memory_curator;

GRANT SELECT, INSERT ON tasks, approvals TO egoagentos_runtime;
GRANT UPDATE(generation, version, task_json, created_at, updated_at)
    ON tasks TO egoagentos_runtime;
GRANT UPDATE(status, expires_at, token_hash, record_json)
    ON approvals TO egoagentos_runtime;
GRANT SELECT, INSERT ON evidence, memory_candidates, memories, idempotency
    TO egoagentos_runtime;
GRANT SELECT, INSERT ON audit_events TO egoagentos_runtime;
GRANT SELECT ON schema_migrations TO egoagentos_runtime;
GRANT SELECT ON
    tasks, approvals, evidence, memory_candidates, memories, audit_events, idempotency,
    schema_migrations
    TO egoagentos_auditor;
GRANT SELECT ON tasks, approvals, evidence TO egoagentos_evidence_writer;
GRANT INSERT ON evidence TO egoagentos_evidence_writer;
GRANT SELECT ON tasks, evidence, memory_candidates, memories TO egoagentos_memory_curator;
GRANT INSERT ON memory_candidates TO egoagentos_memory_curator;

ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE approvals FORCE ROW LEVEL SECURITY;
ALTER TABLE evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_candidates FORCE ROW LEVEL SECURITY;
ALTER TABLE memories FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE idempotency FORCE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION egoagentos_current_tenant()
RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('egoagentos.tenant_id', true), '')
$$;

REVOKE ALL ON FUNCTION egoagentos_current_tenant() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION egoagentos_current_tenant()
    TO egoagentos_runtime,
       egoagentos_auditor,
       egoagentos_evidence_writer,
       egoagentos_memory_curator;
REVOKE ALL ON FUNCTION egoagentos_guard_audit_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION egoagentos_reject_audit_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION egoagentos_notify_stage_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION egoagentos_reject_ledger_mutation() FROM PUBLIC;

DROP POLICY IF EXISTS tasks_tenant_policy ON tasks;
CREATE POLICY tasks_tenant_policy ON tasks
    USING (tenant_id = egoagentos_current_tenant())
    WITH CHECK (tenant_id = egoagentos_current_tenant());
DROP POLICY IF EXISTS approvals_tenant_policy ON approvals;
CREATE POLICY approvals_tenant_policy ON approvals
    USING (tenant_id = egoagentos_current_tenant())
    WITH CHECK (tenant_id = egoagentos_current_tenant());
DROP POLICY IF EXISTS evidence_tenant_policy ON evidence;
CREATE POLICY evidence_tenant_policy ON evidence
    USING (tenant_id = egoagentos_current_tenant())
    WITH CHECK (tenant_id = egoagentos_current_tenant());
DROP POLICY IF EXISTS memories_tenant_policy ON memories;
CREATE POLICY memories_tenant_policy ON memories
    USING (tenant_id = egoagentos_current_tenant())
    WITH CHECK (tenant_id = egoagentos_current_tenant());
DROP POLICY IF EXISTS memory_candidates_tenant_policy ON memory_candidates;
CREATE POLICY memory_candidates_tenant_policy ON memory_candidates
    USING (tenant_id = egoagentos_current_tenant())
    WITH CHECK (tenant_id = egoagentos_current_tenant());
DROP POLICY IF EXISTS audit_events_tenant_policy ON audit_events;
CREATE POLICY audit_events_tenant_policy ON audit_events
    USING (tenant_id = egoagentos_current_tenant())
    WITH CHECK (tenant_id = egoagentos_current_tenant());
DROP POLICY IF EXISTS idempotency_tenant_policy ON idempotency;
CREATE POLICY idempotency_tenant_policy ON idempotency
    USING (tenant_id = egoagentos_current_tenant())
    WITH CHECK (tenant_id = egoagentos_current_tenant());

-- Compose and deployment automation create hardened LOGIN identities separately, source
-- passwords from secret storage, grant these NOLOGIN roles, and run the API in verify mode.
