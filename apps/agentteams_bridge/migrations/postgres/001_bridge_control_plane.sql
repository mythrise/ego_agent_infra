CREATE TABLE bridge_runs (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    ego_task_id TEXT NOT NULL CHECK (length(ego_task_id) > 0),
    agentteams_project_id TEXT NOT NULL UNIQUE CHECK (length(agentteams_project_id) > 0),
    team TEXT NOT NULL CHECK (length(team) > 0),
    trace_id TEXT NOT NULL CHECK (length(trace_id) > 0),
    correlation_id TEXT NOT NULL CHECK (length(correlation_id) > 0),
    context_version INTEGER NOT NULL CHECK (context_version >= 1),
    state TEXT NOT NULL CHECK (state IN (
        'PROVISIONING', 'PRE_APPROVAL', 'WAITING_R2', 'POST_APPROVAL',
        'COMPENSATION_REQUIRED', 'BLOCKED', 'COMPLETED'
    )),
    mode TEXT NOT NULL CHECK (mode IN ('live', 'dry_run')),
    objective TEXT NOT NULL CHECK (length(objective) > 0),
    task_graph JSONB NOT NULL CHECK (jsonb_typeof(task_graph) = 'array'),
    checkpoint JSONB NOT NULL CHECK (jsonb_typeof(checkpoint) = 'object'),
    ack_timeout_seconds INTEGER NOT NULL CHECK (ack_timeout_seconds BETWEEN 5 AND 86400),
    execution_timeout_seconds INTEGER NOT NULL CHECK (
        execution_timeout_seconds BETWEEN 30 AND 604800
    ),
    max_reassignments INTEGER NOT NULL CHECK (max_reassignments BETWEEN 0 AND 10),
    version INTEGER NOT NULL CHECK (version >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_bridge_runs_task
    ON bridge_runs(ego_task_id);

CREATE TABLE bridge_events (
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    event_id TEXT NOT NULL UNIQUE CHECK (length(event_id) > 0),
    run_id TEXT NOT NULL REFERENCES bridge_runs(id),
    kind TEXT NOT NULL CHECK (kind IN (
        'TASK_REQUEST', 'TASK_UPDATE', 'ARTIFACT_ACCEPTED', 'CONFLICT',
        'REPLAN', 'APPROVAL_REQUIRED', 'APPROVAL_GRANTED', 'COMPENSATION', 'TERMINAL'
    )),
    envelope JSONB NOT NULL CHECK (jsonb_typeof(envelope) = 'object'),
    previous_hash TEXT NOT NULL CHECK (previous_hash ~ '^[0-9a-f]{64}$'),
    event_hash TEXT NOT NULL UNIQUE CHECK (event_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(run_id, sequence)
);

CREATE TABLE bridge_receipts (
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    receipt_id TEXT NOT NULL UNIQUE CHECK (length(receipt_id) > 0),
    run_id TEXT NOT NULL REFERENCES bridge_runs(id),
    receipt_key TEXT NOT NULL CHECK (length(receipt_key) > 0),
    source TEXT NOT NULL CHECK (length(source) > 0),
    kind TEXT NOT NULL CHECK (length(kind) > 0),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    previous_hash TEXT NOT NULL CHECK (previous_hash ~ '^[0-9a-f]{64}$'),
    receipt_hash TEXT NOT NULL UNIQUE CHECK (receipt_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(run_id, sequence),
    UNIQUE(run_id, receipt_key)
);

CREATE OR REPLACE FUNCTION egoagentos_bridge_guard_event_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    expected_sequence BIGINT;
    expected_previous_hash TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('egoagentos:bridge:event:' || NEW.run_id, 0)
    );
    SELECT sequence, event_hash INTO expected_sequence, expected_previous_hash
      FROM bridge_events
     WHERE run_id = NEW.run_id
     ORDER BY sequence DESC
     LIMIT 1;
    expected_sequence := COALESCE(expected_sequence, 0) + 1;
    IF NEW.sequence IS NOT NULL AND NEW.sequence IS DISTINCT FROM expected_sequence THEN
        RAISE EXCEPTION 'bridge event sequence mismatch for run %', NEW.run_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.sequence := expected_sequence;
    expected_previous_hash := COALESCE(expected_previous_hash, repeat('0', 64));
    IF NEW.previous_hash IS DISTINCT FROM expected_previous_hash THEN
        RAISE EXCEPTION 'bridge event predecessor mismatch for run %', NEW.run_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION egoagentos_bridge_guard_receipt_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    expected_sequence BIGINT;
    expected_previous_hash TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('egoagentos:bridge:receipt:' || NEW.run_id, 0)
    );
    SELECT sequence, receipt_hash INTO expected_sequence, expected_previous_hash
      FROM bridge_receipts
     WHERE run_id = NEW.run_id
     ORDER BY sequence DESC
     LIMIT 1;
    expected_sequence := COALESCE(expected_sequence, 0) + 1;
    IF NEW.sequence IS NOT NULL AND NEW.sequence IS DISTINCT FROM expected_sequence THEN
        RAISE EXCEPTION 'bridge receipt sequence mismatch for run %', NEW.run_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.sequence := expected_sequence;
    expected_previous_hash := COALESCE(expected_previous_hash, repeat('0', 64));
    IF NEW.previous_hash IS DISTINCT FROM expected_previous_hash THEN
        RAISE EXCEPTION 'bridge receipt predecessor mismatch for run %', NEW.run_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION egoagentos_bridge_reject_ledger_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'bridge ledger rows are append-only'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS bridge_events_guard_insert ON bridge_events;
CREATE TRIGGER bridge_events_guard_insert
BEFORE INSERT ON bridge_events
FOR EACH ROW EXECUTE FUNCTION egoagentos_bridge_guard_event_insert();

DROP TRIGGER IF EXISTS bridge_events_no_update_or_delete ON bridge_events;
CREATE TRIGGER bridge_events_no_update_or_delete
BEFORE UPDATE OR DELETE ON bridge_events
FOR EACH ROW EXECUTE FUNCTION egoagentos_bridge_reject_ledger_mutation();

DROP TRIGGER IF EXISTS bridge_events_no_truncate ON bridge_events;
CREATE TRIGGER bridge_events_no_truncate
BEFORE TRUNCATE ON bridge_events
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_bridge_reject_ledger_mutation();

DROP TRIGGER IF EXISTS bridge_receipts_guard_insert ON bridge_receipts;
CREATE TRIGGER bridge_receipts_guard_insert
BEFORE INSERT ON bridge_receipts
FOR EACH ROW EXECUTE FUNCTION egoagentos_bridge_guard_receipt_insert();

DROP TRIGGER IF EXISTS bridge_receipts_no_update_or_delete ON bridge_receipts;
CREATE TRIGGER bridge_receipts_no_update_or_delete
BEFORE UPDATE OR DELETE ON bridge_receipts
FOR EACH ROW EXECUTE FUNCTION egoagentos_bridge_reject_ledger_mutation();

DROP TRIGGER IF EXISTS bridge_receipts_no_truncate ON bridge_receipts;
CREATE TRIGGER bridge_receipts_no_truncate
BEFORE TRUNCATE ON bridge_receipts
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_bridge_reject_ledger_mutation();
