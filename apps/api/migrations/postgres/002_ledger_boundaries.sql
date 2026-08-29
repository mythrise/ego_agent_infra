CREATE TABLE IF NOT EXISTS memory_candidates (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'local',
    task_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    evidence_digest TEXT NOT NULL CHECK (evidence_digest ~ '^[0-9a-f]{64}$'),
    review_id TEXT NOT NULL,
    record_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(tenant_id, id)
);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_task_generation
    ON memory_candidates(tenant_id, task_id, generation, created_at, id);

CREATE OR REPLACE FUNCTION egoagentos_reject_ledger_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% ledger is append-only', TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS evidence_no_update_or_delete ON evidence;
CREATE TRIGGER evidence_no_update_or_delete
BEFORE UPDATE OR DELETE ON evidence
FOR EACH ROW EXECUTE FUNCTION egoagentos_reject_ledger_mutation();

DROP TRIGGER IF EXISTS evidence_no_truncate ON evidence;
CREATE TRIGGER evidence_no_truncate
BEFORE TRUNCATE ON evidence
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_reject_ledger_mutation();

DROP TRIGGER IF EXISTS memory_candidates_no_update_or_delete ON memory_candidates;
CREATE TRIGGER memory_candidates_no_update_or_delete
BEFORE UPDATE OR DELETE ON memory_candidates
FOR EACH ROW EXECUTE FUNCTION egoagentos_reject_ledger_mutation();

DROP TRIGGER IF EXISTS memory_candidates_no_truncate ON memory_candidates;
CREATE TRIGGER memory_candidates_no_truncate
BEFORE TRUNCATE ON memory_candidates
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_reject_ledger_mutation();

DROP TRIGGER IF EXISTS memories_no_update_or_delete ON memories;
CREATE TRIGGER memories_no_update_or_delete
BEFORE UPDATE OR DELETE ON memories
FOR EACH ROW EXECUTE FUNCTION egoagentos_reject_ledger_mutation();

DROP TRIGGER IF EXISTS memories_no_truncate ON memories;
CREATE TRIGGER memories_no_truncate
BEFORE TRUNCATE ON memories
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_reject_ledger_mutation();
