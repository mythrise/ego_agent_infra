CREATE TABLE IF NOT EXISTS trusted_memory_streams (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    sequence BIGINT NOT NULL CHECK (sequence >= 0),
    stream_root TEXT NOT NULL CHECK (stream_root ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY(tenant_id, project_id, lineage_id)
);

CREATE TABLE IF NOT EXISTS trusted_memory_history (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    event_type TEXT NOT NULL,
    record_bytes BYTEA NOT NULL,
    record_sha256 TEXT NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    previous_hash TEXT NOT NULL CHECK (previous_hash ~ '^[0-9a-f]{64}$'),
    event_hash TEXT NOT NULL UNIQUE CHECK (event_hash ~ '^[0-9a-f]{64}$'),
    idempotency_key TEXT NOT NULL,
    PRIMARY KEY(tenant_id, project_id, lineage_id, sequence),
    UNIQUE(tenant_id, project_id, lineage_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS trusted_memory_current (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision > 0),
    fact_digest TEXT NOT NULL CHECK (fact_digest ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL,
    eligible BOOLEAN NOT NULL,
    fact_bytes BYTEA NOT NULL,
    fact_event_hash TEXT NOT NULL CHECK (fact_event_hash ~ '^[0-9a-f]{64}$'),
    projection_event_hash TEXT NOT NULL CHECK (projection_event_hash ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY(tenant_id, project_id, lineage_id)
);

CREATE TABLE IF NOT EXISTS trusted_memory_closures (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    event_hash TEXT NOT NULL CHECK (event_hash ~ '^[0-9a-f]{64}$'),
    closure_digest TEXT NOT NULL CHECK (closure_digest ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY(tenant_id, project_id, lineage_id, event_hash, closure_digest)
);

CREATE TABLE IF NOT EXISTS trusted_memory_outbox (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    event_hash TEXT NOT NULL UNIQUE CHECK (event_hash ~ '^[0-9a-f]{64}$'),
    payload_json JSONB NOT NULL,
    PRIMARY KEY(tenant_id, project_id, lineage_id, sequence)
);

CREATE OR REPLACE FUNCTION egoagentos_guard_trusted_memory_history_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    expected_sequence BIGINT;
    expected_previous_hash TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            NEW.tenant_id || chr(31) || NEW.project_id || chr(31) || NEW.lineage_id,
            0
        )
    );
    SELECT sequence + 1, stream_root
      INTO expected_sequence, expected_previous_hash
      FROM trusted_memory_streams
     WHERE tenant_id = NEW.tenant_id
       AND project_id = NEW.project_id
       AND lineage_id = NEW.lineage_id
     FOR UPDATE;
    expected_sequence := COALESCE(expected_sequence, 1);
    expected_previous_hash := COALESCE(expected_previous_hash, repeat('0', 64));
    IF NEW.sequence IS DISTINCT FROM expected_sequence
       OR NEW.previous_hash IS DISTINCT FROM expected_previous_hash THEN
        RAISE EXCEPTION 'trusted memory predecessor mismatch'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trusted_memory_history_guard_insert ON trusted_memory_history;
CREATE TRIGGER trusted_memory_history_guard_insert
BEFORE INSERT ON trusted_memory_history
FOR EACH ROW EXECUTE FUNCTION egoagentos_guard_trusted_memory_history_insert();

CREATE OR REPLACE FUNCTION egoagentos_guard_trusted_memory_stream_root()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM trusted_memory_history
         WHERE tenant_id = NEW.tenant_id
           AND project_id = NEW.project_id
           AND lineage_id = NEW.lineage_id
           AND sequence = NEW.sequence
           AND event_hash = NEW.stream_root
    ) THEN
        RAISE EXCEPTION 'trusted memory stream root must name exact history event'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trusted_memory_stream_root_guard ON trusted_memory_streams;
CREATE TRIGGER trusted_memory_stream_root_guard
BEFORE INSERT OR UPDATE ON trusted_memory_streams
FOR EACH ROW EXECUTE FUNCTION egoagentos_guard_trusted_memory_stream_root();

CREATE OR REPLACE FUNCTION egoagentos_guard_trusted_memory_current()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.projection_event_hash = OLD.projection_event_hash THEN
        RAISE EXCEPTION 'trusted memory current projection requires compare-and-swap event'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM trusted_memory_history AS history
          JOIN trusted_memory_streams AS stream
            ON stream.tenant_id = history.tenant_id
           AND stream.project_id = history.project_id
           AND stream.lineage_id = history.lineage_id
           AND stream.sequence = history.sequence
           AND stream.stream_root = history.event_hash
         WHERE history.tenant_id = NEW.tenant_id
           AND history.project_id = NEW.project_id
           AND history.lineage_id = NEW.lineage_id
           AND history.event_hash = NEW.projection_event_hash
    ) THEN
        RAISE EXCEPTION 'trusted memory current projection requires latest history event'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trusted_memory_current_guard ON trusted_memory_current;
CREATE TRIGGER trusted_memory_current_guard
BEFORE INSERT OR UPDATE ON trusted_memory_current
FOR EACH ROW EXECUTE FUNCTION egoagentos_guard_trusted_memory_current();

CREATE OR REPLACE FUNCTION egoagentos_reject_trusted_memory_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'trusted memory ledger is immutable'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS trusted_memory_history_no_update_or_delete ON trusted_memory_history;
CREATE TRIGGER trusted_memory_history_no_update_or_delete
BEFORE UPDATE OR DELETE ON trusted_memory_history
FOR EACH ROW EXECUTE FUNCTION egoagentos_reject_trusted_memory_mutation();
DROP TRIGGER IF EXISTS trusted_memory_history_no_truncate ON trusted_memory_history;
CREATE TRIGGER trusted_memory_history_no_truncate
BEFORE TRUNCATE ON trusted_memory_history
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_reject_trusted_memory_mutation();

DROP TRIGGER IF EXISTS trusted_memory_closures_no_update_or_delete ON trusted_memory_closures;
CREATE TRIGGER trusted_memory_closures_no_update_or_delete
BEFORE UPDATE OR DELETE ON trusted_memory_closures
FOR EACH ROW EXECUTE FUNCTION egoagentos_reject_trusted_memory_mutation();
DROP TRIGGER IF EXISTS trusted_memory_closures_no_truncate ON trusted_memory_closures;
CREATE TRIGGER trusted_memory_closures_no_truncate
BEFORE TRUNCATE ON trusted_memory_closures
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_reject_trusted_memory_mutation();

DROP TRIGGER IF EXISTS trusted_memory_outbox_no_update_or_delete ON trusted_memory_outbox;
CREATE TRIGGER trusted_memory_outbox_no_update_or_delete
BEFORE UPDATE OR DELETE ON trusted_memory_outbox
FOR EACH ROW EXECUTE FUNCTION egoagentos_reject_trusted_memory_mutation();
DROP TRIGGER IF EXISTS trusted_memory_outbox_no_truncate ON trusted_memory_outbox;
CREATE TRIGGER trusted_memory_outbox_no_truncate
BEFORE TRUNCATE ON trusted_memory_outbox
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_reject_trusted_memory_mutation();

DROP TRIGGER IF EXISTS trusted_memory_streams_no_delete ON trusted_memory_streams;
CREATE TRIGGER trusted_memory_streams_no_delete
BEFORE DELETE OR TRUNCATE ON trusted_memory_streams
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_reject_trusted_memory_mutation();

DROP TRIGGER IF EXISTS trusted_memory_current_no_delete ON trusted_memory_current;
CREATE TRIGGER trusted_memory_current_no_delete
BEFORE DELETE OR TRUNCATE ON trusted_memory_current
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_reject_trusted_memory_mutation();

CREATE OR REPLACE FUNCTION egoagentos_notify_trusted_memory_event()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_notify('ego_trusted_memory_events', NEW.payload_json::text);
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trusted_memory_outbox_notify ON trusted_memory_outbox;
CREATE TRIGGER trusted_memory_outbox_notify
AFTER INSERT ON trusted_memory_outbox
FOR EACH ROW EXECUTE FUNCTION egoagentos_notify_trusted_memory_event();

ALTER TABLE trusted_memory_streams ENABLE ROW LEVEL SECURITY;
ALTER TABLE trusted_memory_streams FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS trusted_memory_streams_tenant ON trusted_memory_streams;
CREATE POLICY trusted_memory_streams_tenant ON trusted_memory_streams
USING (tenant_id = current_setting('egoagentos.tenant_id', true))
WITH CHECK (tenant_id = current_setting('egoagentos.tenant_id', true));

ALTER TABLE trusted_memory_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE trusted_memory_history FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS trusted_memory_history_tenant ON trusted_memory_history;
CREATE POLICY trusted_memory_history_tenant ON trusted_memory_history
USING (tenant_id = current_setting('egoagentos.tenant_id', true))
WITH CHECK (tenant_id = current_setting('egoagentos.tenant_id', true));

ALTER TABLE trusted_memory_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE trusted_memory_current FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS trusted_memory_current_tenant ON trusted_memory_current;
CREATE POLICY trusted_memory_current_tenant ON trusted_memory_current
USING (tenant_id = current_setting('egoagentos.tenant_id', true))
WITH CHECK (tenant_id = current_setting('egoagentos.tenant_id', true));

ALTER TABLE trusted_memory_closures ENABLE ROW LEVEL SECURITY;
ALTER TABLE trusted_memory_closures FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS trusted_memory_closures_tenant ON trusted_memory_closures;
CREATE POLICY trusted_memory_closures_tenant ON trusted_memory_closures
USING (tenant_id = current_setting('egoagentos.tenant_id', true))
WITH CHECK (tenant_id = current_setting('egoagentos.tenant_id', true));

ALTER TABLE trusted_memory_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE trusted_memory_outbox FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS trusted_memory_outbox_tenant ON trusted_memory_outbox;
CREATE POLICY trusted_memory_outbox_tenant ON trusted_memory_outbox
USING (tenant_id = current_setting('egoagentos.tenant_id', true))
WITH CHECK (tenant_id = current_setting('egoagentos.tenant_id', true));

DO $grants$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'egoagentos_memory_reader') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA public TO egoagentos_memory_reader';
        EXECUTE 'GRANT SELECT ON trusted_memory_streams, trusted_memory_history, trusted_memory_current, trusted_memory_closures, trusted_memory_outbox TO egoagentos_memory_reader';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'egoagentos_memory_writer') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA public TO egoagentos_memory_writer';
        EXECUTE 'GRANT SELECT ON trusted_memory_streams, trusted_memory_history, trusted_memory_current, trusted_memory_closures, trusted_memory_outbox TO egoagentos_memory_writer';
        EXECUTE 'GRANT INSERT ON trusted_memory_history, trusted_memory_closures, trusted_memory_outbox TO egoagentos_memory_writer';
        EXECUTE 'GRANT INSERT ON trusted_memory_streams, trusted_memory_current TO egoagentos_memory_writer';
        EXECUTE 'GRANT UPDATE ON trusted_memory_streams, trusted_memory_current TO egoagentos_memory_writer';
    END IF;
END
$grants$;
