ALTER TABLE bridge_runs
    ADD COLUMN tenant_id TEXT,
    ADD COLUMN campaign_binding JSONB,
    ADD COLUMN campaign_binding_sha256 TEXT,
    ADD COLUMN campaign_id TEXT,
    ADD COLUMN configuration_id TEXT,
    ADD COLUMN execution_phase_owner TEXT,
    ADD COLUMN problem_id TEXT,
    ADD COLUMN campaign_turn INTEGER,
    ADD COLUMN campaign_generation INTEGER,
    ADD COLUMN manifest_sha256 TEXT,
    ADD COLUMN post_selection_extension_sha256 TEXT,
    ADD COLUMN policy_sha256 TEXT,
    ADD COLUMN requirement_ledger_sha256 TEXT,
    ADD COLUMN workspace_checkpoint_sha256 TEXT,
    ADD COLUMN memory_watermark BIGINT;

ALTER TABLE bridge_runs
    ADD CONSTRAINT bridge_runs_campaign_binding_shape CHECK (
        campaign_binding IS NULL OR (
            jsonb_typeof(campaign_binding) = 'object'
            AND campaign_binding_sha256 ~ '^[0-9a-f]{64}$'
            AND length(campaign_id) > 0
            AND execution_phase_owner IN (
                'A', 'B', 'C', 'D', 'E', 'F', 'QUALIFICATION', 'OPTIMIZER',
                'WINNER_SEALED', 'F_SEALED', 'GPU_DEMO'
            )
            AND length(problem_id) > 0
            AND campaign_turn BETWEEN 1 AND 5
            AND campaign_generation >= 1
            AND manifest_sha256 ~ '^[0-9a-f]{64}$'
            AND policy_sha256 ~ '^[0-9a-f]{64}$'
            AND requirement_ledger_sha256 ~ '^[0-9a-f]{64}$'
            AND workspace_checkpoint_sha256 ~ '^[0-9a-f]{64}$'
            AND memory_watermark >= 0
        )
    ),
    ADD CONSTRAINT bridge_runs_campaign_configuration_shape CHECK (
        campaign_binding IS NULL OR (
            (execution_phase_owner IN ('A', 'B', 'C', 'D', 'E')
                AND configuration_id = execution_phase_owner
                AND post_selection_extension_sha256 IS NULL)
            OR (execution_phase_owner IN ('F', 'F_SEALED')
                AND configuration_id = 'F'
                AND post_selection_extension_sha256 ~ '^[0-9a-f]{64}$')
            OR (execution_phase_owner = 'WINNER_SEALED'
                AND configuration_id IN ('C', 'D', 'E')
                AND post_selection_extension_sha256 ~ '^[0-9a-f]{64}$')
            OR (execution_phase_owner IN ('QUALIFICATION', 'OPTIMIZER')
                AND configuration_id IS NULL
                AND post_selection_extension_sha256 IS NULL)
            OR (execution_phase_owner = 'GPU_DEMO'
                AND configuration_id IN ('C', 'D', 'E', 'F')
                AND (
                    (configuration_id = 'F'
                        AND post_selection_extension_sha256 ~ '^[0-9a-f]{64}$')
                    OR (configuration_id <> 'F'
                        AND post_selection_extension_sha256 IS NULL)
                ))
        )
    );

CREATE TABLE bridge_extension_events (
    run_id TEXT NOT NULL REFERENCES bridge_runs(id),
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    event_id TEXT NOT NULL UNIQUE CHECK (length(event_id) > 0),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'VERIFIED_TASK_LEASE_ADMISSION', 'VERIFIED_EVALUATOR_ADMISSION',
        'CANONICAL_EFFECT', 'SYSTEM_RISK_ASSESSMENT', 'GUARDIAN_DECISION',
        'SAFETY_DECISION', 'ATTENTION_PACKET', 'USER_STATUS_PROJECTION'
    )),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) > 0),
    canonical_payload BYTEA NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    memory_watermark BIGINT NOT NULL CHECK (memory_watermark >= 0),
    previous_hash TEXT NOT NULL CHECK (previous_hash ~ '^[0-9a-f]{64}$'),
    event_hash TEXT NOT NULL UNIQUE CHECK (event_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(run_id, sequence),
    UNIQUE(run_id, idempotency_key)
);

CREATE TABLE bridge_task_leases (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL CHECK (length(task_id) > 0),
    event_sequence BIGINT NOT NULL,
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) > 0),
    canonical_signed_payload BYTEA NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    signature_base64 TEXT NOT NULL CHECK (length(signature_base64) > 0),
    key_id TEXT NOT NULL CHECK (length(key_id) > 0),
    issuer_id TEXT NOT NULL CHECK (length(issuer_id) > 0),
    previous_stream_digest TEXT NOT NULL CHECK (previous_stream_digest ~ '^[0-9a-f]{64}$'),
    stream_digest TEXT NOT NULL CHECK (stream_digest ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(run_id, task_id),
    UNIQUE(run_id, event_sequence),
    UNIQUE(run_id, idempotency_key),
    FOREIGN KEY(run_id, event_sequence)
        REFERENCES bridge_extension_events(run_id, sequence)
);

CREATE TABLE bridge_evaluator_bindings (
    run_id TEXT NOT NULL,
    binding_id TEXT NOT NULL CHECK (length(binding_id) > 0),
    task_id TEXT NOT NULL CHECK (length(task_id) > 0),
    event_sequence BIGINT NOT NULL,
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) > 0),
    canonical_signed_payload BYTEA NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    signature_base64 TEXT NOT NULL CHECK (length(signature_base64) > 0),
    key_id TEXT NOT NULL CHECK (length(key_id) > 0),
    issuer_id TEXT NOT NULL CHECK (length(issuer_id) > 0),
    previous_stream_digest TEXT NOT NULL CHECK (previous_stream_digest ~ '^[0-9a-f]{64}$'),
    stream_digest TEXT NOT NULL CHECK (stream_digest ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(run_id, binding_id),
    UNIQUE(run_id, event_sequence),
    UNIQUE(run_id, idempotency_key),
    FOREIGN KEY(run_id, task_id) REFERENCES bridge_task_leases(run_id, task_id),
    FOREIGN KEY(run_id, event_sequence)
        REFERENCES bridge_extension_events(run_id, sequence)
);

CREATE INDEX idx_bridge_extension_events_run
    ON bridge_extension_events(run_id, sequence);
CREATE INDEX idx_bridge_task_leases_run
    ON bridge_task_leases(run_id, event_sequence);
CREATE INDEX idx_bridge_evaluator_bindings_run
    ON bridge_evaluator_bindings(run_id, event_sequence);

CREATE OR REPLACE FUNCTION egoagentos_bridge_guard_extension_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    expected_sequence BIGINT;
    expected_previous_hash TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('egoagentos:bridge:extension:' || NEW.run_id, 0)
    );
    SELECT sequence, event_hash INTO expected_sequence, expected_previous_hash
      FROM bridge_extension_events
     WHERE run_id = NEW.run_id
     ORDER BY sequence DESC
     LIMIT 1;
    expected_sequence := COALESCE(expected_sequence, 0) + 1;
    expected_previous_hash := COALESCE(expected_previous_hash, repeat('0', 64));
    IF NEW.sequence IS DISTINCT FROM expected_sequence THEN
        RAISE EXCEPTION 'bridge extension sequence mismatch for run %', NEW.run_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.previous_hash IS DISTINCT FROM expected_previous_hash THEN
        RAISE EXCEPTION 'bridge extension predecessor mismatch for run %', NEW.run_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION egoagentos_bridge_reject_campaign_rebind()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.campaign_binding IS NOT NULL AND ROW(
        NEW.campaign_binding,
        NEW.campaign_binding_sha256,
        NEW.tenant_id,
        NEW.campaign_id,
        NEW.configuration_id,
        NEW.execution_phase_owner,
        NEW.problem_id,
        NEW.campaign_turn,
        NEW.campaign_generation,
        NEW.manifest_sha256,
        NEW.post_selection_extension_sha256,
        NEW.policy_sha256,
        NEW.requirement_ledger_sha256,
        NEW.workspace_checkpoint_sha256,
        NEW.memory_watermark
    ) IS DISTINCT FROM ROW(
        OLD.campaign_binding,
        OLD.campaign_binding_sha256,
        OLD.tenant_id,
        OLD.campaign_id,
        OLD.configuration_id,
        OLD.execution_phase_owner,
        OLD.problem_id,
        OLD.campaign_turn,
        OLD.campaign_generation,
        OLD.manifest_sha256,
        OLD.post_selection_extension_sha256,
        OLD.policy_sha256,
        OLD.requirement_ledger_sha256,
        OLD.workspace_checkpoint_sha256,
        OLD.memory_watermark
    ) THEN
        RAISE EXCEPTION 'bridge campaign binding is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION egoagentos_bridge_notify_extension()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_notify(
        'egoagentos_bridge_extension',
        json_build_object(
            'run_id', NEW.run_id,
            'sequence', NEW.sequence,
            'event_id', NEW.event_id,
            'event_hash', NEW.event_hash
        )::text
    );
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION egoagentos_bridge_scope_allows(target_run_id TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1
         FROM bridge_runs AS run
         WHERE run.id = target_run_id
           AND NULLIF(current_setting('egoagentos.tenant_id', true), '') IS NOT NULL
           AND run.tenant_id = current_setting('egoagentos.tenant_id', true)
           AND NULLIF(current_setting('egoagentos.project_id', true), '') IS NOT NULL
           AND run.agentteams_project_id = current_setting('egoagentos.project_id', true)
    )
$$;

CREATE TRIGGER bridge_runs_campaign_no_update
BEFORE UPDATE ON bridge_runs
FOR EACH ROW EXECUTE FUNCTION egoagentos_bridge_reject_campaign_rebind();

CREATE TRIGGER bridge_extension_events_guard_insert
BEFORE INSERT ON bridge_extension_events
FOR EACH ROW EXECUTE FUNCTION egoagentos_bridge_guard_extension_insert();

CREATE TRIGGER bridge_extension_events_notify
AFTER INSERT ON bridge_extension_events
FOR EACH ROW EXECUTE FUNCTION egoagentos_bridge_notify_extension();

CREATE TRIGGER bridge_extension_events_no_update_or_delete
BEFORE UPDATE OR DELETE ON bridge_extension_events
FOR EACH ROW EXECUTE FUNCTION egoagentos_bridge_reject_ledger_mutation();
CREATE TRIGGER bridge_extension_events_no_truncate
BEFORE TRUNCATE ON bridge_extension_events
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_bridge_reject_ledger_mutation();

CREATE TRIGGER bridge_task_leases_no_update_or_delete
BEFORE UPDATE OR DELETE ON bridge_task_leases
FOR EACH ROW EXECUTE FUNCTION egoagentos_bridge_reject_ledger_mutation();
CREATE TRIGGER bridge_task_leases_no_truncate
BEFORE TRUNCATE ON bridge_task_leases
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_bridge_reject_ledger_mutation();

CREATE TRIGGER bridge_evaluator_bindings_no_update_or_delete
BEFORE UPDATE OR DELETE ON bridge_evaluator_bindings
FOR EACH ROW EXECUTE FUNCTION egoagentos_bridge_reject_ledger_mutation();
CREATE TRIGGER bridge_evaluator_bindings_no_truncate
BEFORE TRUNCATE ON bridge_evaluator_bindings
FOR EACH STATEMENT EXECUTE FUNCTION egoagentos_bridge_reject_ledger_mutation();

ALTER TABLE bridge_extension_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE bridge_extension_events FORCE ROW LEVEL SECURITY;
ALTER TABLE bridge_task_leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE bridge_task_leases FORCE ROW LEVEL SECURITY;
ALTER TABLE bridge_evaluator_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE bridge_evaluator_bindings FORCE ROW LEVEL SECURITY;

CREATE POLICY bridge_extension_events_scope ON bridge_extension_events
    USING (egoagentos_bridge_scope_allows(run_id))
    WITH CHECK (egoagentos_bridge_scope_allows(run_id));
CREATE POLICY bridge_task_leases_scope ON bridge_task_leases
    USING (egoagentos_bridge_scope_allows(run_id))
    WITH CHECK (egoagentos_bridge_scope_allows(run_id));
CREATE POLICY bridge_evaluator_bindings_scope ON bridge_evaluator_bindings
    USING (egoagentos_bridge_scope_allows(run_id))
    WITH CHECK (egoagentos_bridge_scope_allows(run_id));
