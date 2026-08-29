CREATE TABLE IF NOT EXISTS tasks (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'local',
    generation TEXT NOT NULL,
    version BIGINT NOT NULL CHECK (version > 0),
    task_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(tenant_id, id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'local',
    task_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    token_hash TEXT,
    record_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(tenant_id, id)
);
CREATE INDEX IF NOT EXISTS idx_approvals_task_generation
    ON approvals(tenant_id, task_id, generation, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_token_hash
    ON approvals(token_hash) WHERE token_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'local',
    task_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    kind TEXT NOT NULL,
    artifact_digest TEXT NOT NULL CHECK (artifact_digest ~ '^[0-9a-f]{64}$'),
    record_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(tenant_id, id)
);
CREATE INDEX IF NOT EXISTS idx_evidence_task_generation
    ON evidence(tenant_id, task_id, generation, created_at, id);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'local',
    task_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    validated BOOLEAN NOT NULL,
    record_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(tenant_id, id)
);
CREATE INDEX IF NOT EXISTS idx_memories_task_generation
    ON memories(tenant_id, task_id, generation, created_at, id);

CREATE TABLE IF NOT EXISTS audit_events (
    sequence BIGINT NOT NULL,
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'local',
    task_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    stage TEXT,
    payload_json JSONB NOT NULL,
    previous_hash TEXT NOT NULL CHECK (previous_hash ~ '^[0-9a-f]{64}$'),
    event_hash TEXT NOT NULL UNIQUE CHECK (event_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(tenant_id, task_id, generation, sequence),
    UNIQUE(tenant_id, id)
);

CREATE TABLE IF NOT EXISTS idempotency (
    tenant_id TEXT NOT NULL DEFAULT 'local',
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    response_json JSONB NOT NULL,
    status_code INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(tenant_id, method, path, key)
);

CREATE OR REPLACE FUNCTION egoagentos_guard_audit_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    expected_sequence BIGINT;
    expected_previous_hash TEXT;
BEGIN
    -- This lock is transaction-scoped and stream-scoped. It also protects direct SQL
    -- writers, not only writes made through the Python store.
    PERFORM pg_advisory_xact_lock(
        hashtextextended(NEW.tenant_id || chr(31) || NEW.task_id || chr(31) || NEW.generation, 0)
    );

    SELECT sequence, event_hash INTO expected_sequence, expected_previous_hash
      FROM audit_events
     WHERE tenant_id = NEW.tenant_id
       AND task_id = NEW.task_id
       AND generation = NEW.generation
     ORDER BY sequence DESC
     LIMIT 1;

    expected_sequence := COALESCE(expected_sequence, 0) + 1;
    IF NEW.sequence IS NOT NULL AND NEW.sequence IS DISTINCT FROM expected_sequence THEN
        RAISE EXCEPTION 'audit sequence mismatch for task % generation %',
            NEW.task_id, NEW.generation
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.sequence := expected_sequence;
    expected_previous_hash := COALESCE(expected_previous_hash, repeat('0', 64));
    IF NEW.previous_hash IS DISTINCT FROM expected_previous_hash THEN
        RAISE EXCEPTION 'audit predecessor mismatch for task % generation %',
            NEW.task_id, NEW.generation
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS audit_events_guard_insert ON audit_events;
CREATE TRIGGER audit_events_guard_insert
BEFORE INSERT ON audit_events
FOR EACH ROW EXECUTE FUNCTION egoagentos_guard_audit_insert();

CREATE OR REPLACE FUNCTION egoagentos_reject_audit_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit events are immutable'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS audit_events_no_update_or_delete ON audit_events;
CREATE TRIGGER audit_events_no_update_or_delete
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION egoagentos_reject_audit_mutation();

DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events;
CREATE TRIGGER audit_events_no_truncate
BEFORE TRUNCATE ON audit_events
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_reject_audit_mutation();

CREATE OR REPLACE FUNCTION egoagentos_notify_stage_event()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- PostgreSQL delivers NOTIFY only after the surrounding transaction commits.
    PERFORM pg_notify(
        'ego_stage_events',
        json_build_object(
            'sequence', NEW.sequence,
            'id', NEW.id,
            'tenant_id', NEW.tenant_id,
            'task_id', NEW.task_id,
            'generation', NEW.generation,
            'event_type', NEW.event_type,
            'stage', NEW.stage,
            'event_hash', NEW.event_hash,
            'created_at', NEW.created_at
        )::text
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS audit_events_stage_notify ON audit_events;
CREATE TRIGGER audit_events_stage_notify
AFTER INSERT ON audit_events
FOR EACH ROW EXECUTE FUNCTION egoagentos_notify_stage_event();
