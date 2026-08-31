-- Run as a database/platform administrator after the checksummed bridge migration.
-- Role creation may require privileges beyond the bridge migration owner. Create a
-- LOGIN identity separately through the platform secret manager, then grant this
-- NOLOGIN role to it. The runtime role must not own these tables or functions.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'egoagentos_bridge_runtime') THEN
        CREATE ROLE egoagentos_bridge_runtime NOLOGIN;
    END IF;
END
$$;

ALTER ROLE egoagentos_bridge_runtime
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

DO $$
BEGIN
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO egoagentos_bridge_runtime',
        current_database()
    );
END
$$;

GRANT USAGE ON SCHEMA public TO egoagentos_bridge_runtime;
REVOKE ALL ON
    bridge_runs,
    bridge_events,
    bridge_receipts,
    bridge_extension_events,
    bridge_task_leases,
    bridge_evaluator_bindings,
    bridge_schema_migrations
FROM egoagentos_bridge_runtime;

GRANT SELECT, INSERT ON bridge_runs TO egoagentos_bridge_runtime;
GRANT UPDATE(
    state,
    task_graph,
    checkpoint,
    version,
    updated_at,
    tenant_id,
    campaign_binding,
    campaign_binding_sha256,
    campaign_id,
    configuration_id,
    execution_phase_owner,
    problem_id,
    campaign_turn,
    campaign_generation,
    manifest_sha256,
    post_selection_extension_sha256,
    policy_sha256,
    requirement_ledger_sha256,
    workspace_checkpoint_sha256,
    memory_watermark
)
    ON bridge_runs TO egoagentos_bridge_runtime;
GRANT SELECT, INSERT ON bridge_events, bridge_receipts TO egoagentos_bridge_runtime;
GRANT SELECT ON bridge_schema_migrations TO egoagentos_bridge_runtime;
GRANT SELECT, INSERT ON
    bridge_extension_events,
    bridge_task_leases,
    bridge_evaluator_bindings
TO egoagentos_bridge_runtime;

REVOKE ALL ON FUNCTION egoagentos_bridge_guard_event_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION egoagentos_bridge_guard_receipt_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION egoagentos_bridge_reject_ledger_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION egoagentos_bridge_guard_extension_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION egoagentos_bridge_notify_extension() FROM PUBLIC;
REVOKE ALL ON FUNCTION egoagentos_bridge_reject_campaign_rebind() FROM PUBLIC;
REVOKE ALL ON FUNCTION egoagentos_bridge_scope_allows(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION egoagentos_bridge_guard_event_insert()
    TO egoagentos_bridge_runtime;
GRANT EXECUTE ON FUNCTION egoagentos_bridge_guard_receipt_insert()
    TO egoagentos_bridge_runtime;
GRANT EXECUTE ON FUNCTION egoagentos_bridge_reject_ledger_mutation()
    TO egoagentos_bridge_runtime;
GRANT EXECUTE ON FUNCTION egoagentos_bridge_guard_extension_insert()
    TO egoagentos_bridge_runtime;
GRANT EXECUTE ON FUNCTION egoagentos_bridge_notify_extension()
    TO egoagentos_bridge_runtime;
GRANT EXECUTE ON FUNCTION egoagentos_bridge_reject_campaign_rebind()
    TO egoagentos_bridge_runtime;
GRANT EXECUTE ON FUNCTION egoagentos_bridge_scope_allows(TEXT)
    TO egoagentos_bridge_runtime;
