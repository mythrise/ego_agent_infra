CREATE TABLE IF NOT EXISTS trusted_memory_decision_closures (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    closure_digest TEXT NOT NULL CHECK (closure_digest ~ '^[0-9a-f]{64}$'),
    closure_bytes BYTEA NOT NULL,
    closure_bytes_sha256 TEXT NOT NULL CHECK (closure_bytes_sha256 ~ '^[0-9a-f]{64}$'),
    idempotency_key TEXT NOT NULL,
    PRIMARY KEY(tenant_id, project_id, closure_digest),
    UNIQUE(tenant_id, project_id, idempotency_key),
    CHECK (encode(sha256(closure_bytes), 'hex') = closure_bytes_sha256)
);

CREATE OR REPLACE FUNCTION egoagentos_reject_decision_closure_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'trusted memory decision closures are immutable'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS trusted_memory_decision_closures_no_update_or_delete
    ON trusted_memory_decision_closures;
CREATE TRIGGER trusted_memory_decision_closures_no_update_or_delete
BEFORE UPDATE OR DELETE ON trusted_memory_decision_closures
FOR EACH ROW EXECUTE FUNCTION egoagentos_reject_decision_closure_mutation();

DROP TRIGGER IF EXISTS trusted_memory_decision_closures_no_truncate
    ON trusted_memory_decision_closures;
CREATE TRIGGER trusted_memory_decision_closures_no_truncate
BEFORE TRUNCATE ON trusted_memory_decision_closures
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_reject_decision_closure_mutation();

ALTER TABLE trusted_memory_decision_closures ENABLE ROW LEVEL SECURITY;
ALTER TABLE trusted_memory_decision_closures FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS trusted_memory_decision_closures_tenant
    ON trusted_memory_decision_closures;
CREATE POLICY trusted_memory_decision_closures_tenant
ON trusted_memory_decision_closures
USING (tenant_id = current_setting('egoagentos.tenant_id', true))
WITH CHECK (tenant_id = current_setting('egoagentos.tenant_id', true));

DO $grants$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'egoagentos_memory_reader') THEN
        EXECUTE 'GRANT SELECT ON trusted_memory_decision_closures TO egoagentos_memory_reader';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'egoagentos_memory_writer') THEN
        EXECUTE 'GRANT SELECT, INSERT ON trusted_memory_decision_closures TO egoagentos_memory_writer';
    END IF;
END
$grants$;
